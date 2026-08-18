"""Explicit, conservative maintenance for pre-SQLite Cortex filesystem state.

The current SQLite ledger never imports or consults this state.  This module is
intentionally isolated from the ledger and normal runtime: callers must opt in
through the ``manage_orchestration(intent='legacy')`` maintenance intent.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import secrets
import stat
import tarfile
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA = "cortex/legacy-lifecycle/v1"
ARCHIVE_DIRECTORY = "legacy-archives"
ARCHIVE_MANIFEST = "LEGACY_ARCHIVE_MANIFEST.json"
_LEGACY_FILES = frozenset({
    "task-index.json",
    "activations.json",
    "host-sessions.json",
    "resource-claims.json",
})
_LEGACY_FLAT_DIRECTORIES = frozenset({"classification-receipts", "operations"})
_LEGACY_TASK_ROOT_FILES = frozenset({"current.json", "task.json", "baseline-manifest.json", "journal.md"})
_LEGACY_TASK_DIRECTORIES = frozenset({"delegations", "reports", "handoffs", "evidence", "planning", "snapshots", "questions"})


class LegacyLifecycleError(ValueError):
    """A legacy maintenance request is unsafe or malformed."""


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_relative(path: Path) -> str:
    value = path.as_posix()
    if not value or value.startswith("/") or ".." in path.parts:
        raise LegacyLifecycleError("legacy path escapes the maintenance root")
    return value


def _private_directory(path: Path) -> None:
    if path.exists():
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise LegacyLifecycleError("legacy archive directory must be a real directory")
    else:
        path.mkdir(mode=0o700, parents=True)
    os.chmod(path, 0o700, follow_symlinks=False)


def _regular_file(path: Path, label: str) -> os.stat_result:
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise LegacyLifecycleError(f"{label} disappeared during maintenance") from exc
    if stat.S_ISLNK(info.st_mode):
        raise LegacyLifecycleError(f"{label} must not be a symlink")
    if not stat.S_ISREG(info.st_mode):
        raise LegacyLifecycleError(f"{label} must be a regular file")
    return info


def _real_directory(path: Path, label: str) -> os.stat_result:
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise LegacyLifecycleError(f"{label} disappeared during maintenance") from exc
    if stat.S_ISLNK(info.st_mode):
        raise LegacyLifecycleError(f"{label} must not be a symlink")
    if not stat.S_ISDIR(info.st_mode):
        raise LegacyLifecycleError(f"{label} must be a real directory")
    return info


def _digest_file(path: Path, expected: os.stat_result | None = None) -> tuple[str, int]:
    info = expected or _regular_file(path, "legacy file")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
            raise LegacyLifecycleError("legacy file changed while it was inspected")
        digest = hashlib.sha256()
        size = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
        after = os.fstat(descriptor)
        if (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) != (
            opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns,
        ):
            raise LegacyLifecycleError("legacy file changed while it was inspected")
        return digest.hexdigest(), size
    finally:
        os.close(descriptor)


def _task_file_is_known(relative: Path) -> bool:
    parts = relative.parts
    if len(parts) == 1:
        return relative.name in _LEGACY_TASK_ROOT_FILES
    if not parts or parts[0] not in _LEGACY_TASK_DIRECTORIES:
        return False
    # The v7 task layout stored structured JSON and Markdown records under
    # these fixed parents.  Accept no executable, database, or special files.
    return relative.suffix.lower() in {".json", ".md", ".txt"}


def _walk_legacy_task(task_dir: Path, root: Path, issues: list[str]) -> list[Path]:
    _real_directory(task_dir, "legacy task directory")
    names = {entry.name for entry in task_dir.iterdir()}
    if not {"current.json", "task.json"}.issubset(names):
        issues.append(f"{_safe_relative(task_dir.relative_to(root))}: legacy task is missing current.json or task.json")
        return []
    files: list[Path] = []
    for current, directories, filenames in os.walk(task_dir, topdown=True, followlinks=False):
        current_path = Path(current)
        retained_directories: list[str] = []
        for name in sorted(directories):
            child = current_path / name
            try:
                child_info = child.lstat()
            except FileNotFoundError:
                issues.append(f"{_safe_relative(child.relative_to(root))}: disappeared during scan")
                continue
            if stat.S_ISLNK(child_info.st_mode):
                issues.append(f"{_safe_relative(child.relative_to(root))}: symlink")
                continue
            relative = child.relative_to(task_dir)
            if not stat.S_ISDIR(child_info.st_mode) or not relative.parts or relative.parts[0] not in _LEGACY_TASK_DIRECTORIES:
                issues.append(f"{_safe_relative(child.relative_to(root))}: unknown legacy task directory")
                continue
            retained_directories.append(name)
        directories[:] = retained_directories
        for name in sorted(filenames):
            child = current_path / name
            relative = child.relative_to(task_dir)
            try:
                info = child.lstat()
            except FileNotFoundError:
                issues.append(f"{_safe_relative(child.relative_to(root))}: disappeared during scan")
                continue
            if stat.S_ISLNK(info.st_mode):
                issues.append(f"{_safe_relative(child.relative_to(root))}: symlink")
            elif not stat.S_ISREG(info.st_mode):
                issues.append(f"{_safe_relative(child.relative_to(root))}: unsafe special file")
            elif not _task_file_is_known(relative):
                issues.append(f"{_safe_relative(child.relative_to(root))}: unknown legacy task file")
            else:
                files.append(child)
    return files


def _walk_flat_directory(directory: Path, root: Path, issues: list[str]) -> list[Path]:
    _real_directory(directory, "legacy directory")
    files: list[Path] = []
    for entry in sorted(directory.iterdir(), key=lambda item: item.name):
        relative = _safe_relative(entry.relative_to(root))
        try:
            info = entry.lstat()
        except FileNotFoundError:
            issues.append(f"{relative}: disappeared during scan")
            continue
        if stat.S_ISLNK(info.st_mode):
            issues.append(f"{relative}: symlink")
        elif not stat.S_ISREG(info.st_mode) or entry.suffix.lower() != ".json":
            issues.append(f"{relative}: unknown or unsafe legacy directory entry")
        else:
            files.append(entry)
    return files


def _walk_lanes(directory: Path, root: Path, issues: list[str]) -> list[Path]:
    _real_directory(directory, "legacy lanes directory")
    files: list[Path] = []
    for lane in sorted(directory.iterdir(), key=lambda item: item.name):
        relative = _safe_relative(lane.relative_to(root))
        try:
            info = lane.lstat()
        except FileNotFoundError:
            issues.append(f"{relative}: disappeared during scan")
            continue
        if stat.S_ISLNK(info.st_mode):
            issues.append(f"{relative}: symlink")
            continue
        if not stat.S_ISDIR(info.st_mode) or not lane.name or lane.name in {".", ".."}:
            issues.append(f"{relative}: unknown or unsafe lane entry")
            continue
        for record in sorted(lane.iterdir(), key=lambda item: item.name):
            record_relative = _safe_relative(record.relative_to(root))
            try:
                record_info = record.lstat()
            except FileNotFoundError:
                issues.append(f"{record_relative}: disappeared during scan")
                continue
            if stat.S_ISLNK(record_info.st_mode):
                issues.append(f"{record_relative}: symlink")
            elif not stat.S_ISREG(record_info.st_mode) or record.name not in {"current.json", "journal.md"}:
                issues.append(f"{record_relative}: unknown or unsafe lane record")
            else:
                files.append(record)
    return files


def _current_task_directories(root: Path) -> set[Path]:
    """Read only the SQLite index to exclude current task artifact paths.

    A missing/corrupt database is treated as no current task mapping here.  The
    scan still requires the legacy signature, and it never treats filesystem
    state as authoritative task state.
    """
    database = root / "cortex.db"
    if not database.exists() or database.is_symlink():
        return set()
    try:
        import sqlite3

        with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
            rows = connection.execute("SELECT artifact_dir FROM tasks").fetchall()
    except Exception:
        return set()
    result: set[Path] = set()
    for (raw_path,) in rows:
        relative = Path(str(raw_path))
        if not relative.is_absolute() and ".." not in relative.parts:
            result.add(root / relative)
    return result


def _scan(root: Path) -> dict[str, Any]:
    """Inventory only fixed pre-SQLite paths; no writes and no state import."""
    if not root.exists():
        return {"schema": SCHEMA, "files": [], "patterns": {}, "issues": [], "root_exists": False}
    _real_directory(root, "Cortex root")
    issues: list[str] = []
    candidates: list[tuple[str, Path]] = []
    for name in sorted(_LEGACY_FILES):
        path = root / name
        if path.exists() or path.is_symlink():
            candidates.append((f"root-file:{name}", path))
    for name in sorted(_LEGACY_FLAT_DIRECTORIES | {"lanes"}):
        path = root / name
        if path.exists() or path.is_symlink():
            candidates.append((f"root-directory:{name}", path))

    files: list[tuple[str, Path]] = []
    for pattern, path in candidates:
        try:
            if path.name in _LEGACY_FILES:
                _regular_file(path, "legacy root file")
                files.append((pattern, path))
            elif path.name in _LEGACY_FLAT_DIRECTORIES:
                files.extend((pattern, item) for item in _walk_flat_directory(path, root, issues))
            else:
                files.extend((pattern, item) for item in _walk_lanes(path, root, issues))
        except LegacyLifecycleError as exc:
            issues.append(f"{_safe_relative(path.relative_to(root))}: {exc}")

    tasks_root = root / "tasks"
    if tasks_root.exists() or tasks_root.is_symlink():
        try:
            _real_directory(tasks_root, "tasks directory")
            current_tasks = _current_task_directories(root)
            for task_dir in sorted(tasks_root.iterdir(), key=lambda item: item.name):
                if task_dir in current_tasks:
                    continue
                try:
                    info = task_dir.lstat()
                except FileNotFoundError:
                    issues.append("tasks entry disappeared during scan")
                    continue
                if stat.S_ISLNK(info.st_mode):
                    # A symlink in an otherwise current task tree is not a
                    # legacy candidate.  A legacy-signature symlink is still
                    # rejected by the explicit task scan below.
                    continue
                if not stat.S_ISDIR(info.st_mode):
                    continue
                names = {entry.name for entry in task_dir.iterdir()}
                if {"current.json", "task.json"}.intersection(names):
                    files.extend(("task-ledger", item) for item in _walk_legacy_task(task_dir, root, issues))
        except LegacyLifecycleError as exc:
            issues.append(str(exc))

    records: list[dict[str, Any]] = []
    patterns: dict[str, dict[str, int]] = {}
    for pattern, path in sorted(files, key=lambda item: _safe_relative(item[1].relative_to(root))):
        try:
            info = _regular_file(path, "legacy file")
            digest, size = _digest_file(path, info)
        except LegacyLifecycleError as exc:
            issues.append(f"{_safe_relative(path.relative_to(root))}: {exc}")
            continue
        relative = _safe_relative(path.relative_to(root))
        records.append({"path": relative, "size": size, "sha256": digest, "pattern": pattern})
        bucket = patterns.setdefault(pattern, {"file_count": 0, "total_bytes": 0})
        bucket["file_count"] += 1
        bucket["total_bytes"] += size
    return {"schema": SCHEMA, "files": records, "patterns": patterns, "issues": sorted(set(issues)), "root_exists": True}


def inventory(project_root: Path) -> dict[str, Any]:
    root = project_root / ".codex" / "cortex"
    scan = _scan(root)
    return {
        "schema": SCHEMA,
        "ok": True,
        "outcome": "legacy_inventory",
        "read_only": True,
        "legacy_root": str(root),
        "file_count": len(scan["files"]),
        "total_bytes": sum(int(item["size"]) for item in scan["files"]),
        "recognized_patterns": scan["patterns"],
        "issues": scan["issues"],
        "safe_to_archive": bool(scan["files"]) and not scan["issues"],
        "current_state_interpreted": False,
        "files": scan["files"],
    }


def _archive_path(root: Path, archive_id: str) -> Path:
    if not archive_id or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789-" for char in archive_id):
        raise LegacyLifecycleError("archive_id is invalid")
    archive_root = root / ARCHIVE_DIRECTORY
    _private_directory(archive_root)
    return archive_root / f"{archive_id}.tar.gz"


def _archive_manifest(root: Path, scan: dict[str, Any], archive_id: str) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "archive_id": archive_id,
        "created_at": _iso_now(),
        "source_root": str(root),
        "files": scan["files"],
        "total_bytes": sum(int(item["size"]) for item in scan["files"]),
        "current_state_interpreted": False,
    }


def _add_verified_file(archive: tarfile.TarFile, root: Path, record: dict[str, Any]) -> None:
    relative = Path(str(record["path"]))
    source = root / relative
    expected = _regular_file(source, "legacy archive source")
    digest, size = _digest_file(source, expected)
    if digest != record["sha256"] or size != int(record["size"]):
        raise LegacyLifecycleError(f"legacy source changed since inventory: {record['path']}")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(source, flags)
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (expected.st_dev, expected.st_ino):
            raise LegacyLifecycleError(f"legacy source changed during archive: {record['path']}")
        entry = tarfile.TarInfo(_safe_relative(relative))
        entry.size = opened.st_size
        entry.mode = 0o600
        entry.mtime = int(opened.st_mtime)
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            archive.addfile(entry, stream)
    finally:
        os.close(descriptor)


def archive(project_root: Path) -> dict[str, Any]:
    root = project_root / ".codex" / "cortex"
    scan = _scan(root)
    if scan["issues"]:
        raise LegacyLifecycleError("legacy archive is blocked: " + "; ".join(scan["issues"]))
    if not scan["files"]:
        raise LegacyLifecycleError("no recognized legacy filesystem state to archive")
    archive_id = "legacy-" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S") + "-" + secrets.token_hex(6)
    destination = _archive_path(root, archive_id)
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0), 0o600)
    manifest = _archive_manifest(root, scan, archive_id)
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            with tarfile.open(fileobj=stream, mode="w:gz", format=tarfile.PAX_FORMAT) as bundle:
                manifest_bytes = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
                manifest_info = tarfile.TarInfo(ARCHIVE_MANIFEST)
                manifest_info.size = len(manifest_bytes)
                manifest_info.mode = 0o600
                bundle.addfile(manifest_info, io.BytesIO(manifest_bytes))
                for record in manifest["files"]:
                    _add_verified_file(bundle, root, record)
        os.chmod(destination, 0o600, follow_symlinks=False)
    except Exception:
        try:
            destination.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return {
        "schema": SCHEMA,
        "ok": True,
        "outcome": "legacy_archived",
        "archive_id": archive_id,
        "archive_path": str(destination),
        "file_count": len(manifest["files"]),
        "total_bytes": manifest["total_bytes"],
        "private_permissions": "0600",
        "current_state_interpreted": False,
        "next_action": f"To delete these archived legacy sources, call intent=legacy with action=delete, archive_id={archive_id}, and confirmation=DELETE_LEGACY_ARCHIVE:{archive_id}.",
    }


def _read_archive_manifest(root: Path, archive_id: str) -> tuple[Path, dict[str, Any]]:
    path = _archive_path(root, archive_id)
    info = _regular_file(path, "legacy archive")
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise LegacyLifecycleError("legacy archive must have private permissions")
    try:
        with tarfile.open(path, mode="r:gz") as bundle:
            member = bundle.getmember(ARCHIVE_MANIFEST)
            extracted = bundle.extractfile(member)
            if extracted is None:
                raise LegacyLifecycleError("legacy archive manifest is unreadable")
            payload = json.loads(extracted.read().decode("utf-8"))
    except (OSError, tarfile.TarError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LegacyLifecycleError("legacy archive is invalid") from exc
    if not isinstance(payload, dict) or payload.get("schema") != SCHEMA or payload.get("archive_id") != archive_id:
        raise LegacyLifecycleError("legacy archive manifest is invalid")
    files = payload.get("files")
    if not isinstance(files, list) or not files:
        raise LegacyLifecycleError("legacy archive has no source inventory")
    for record in files:
        if not isinstance(record, dict) or not isinstance(record.get("path"), str) or not isinstance(record.get("sha256"), str):
            raise LegacyLifecycleError("legacy archive source inventory is invalid")
        _safe_relative(Path(record["path"]))
    return path, payload


def _remove_empty_parents(root: Path, paths: Iterable[Path]) -> None:
    protected = {root, root / "tasks"}
    for item in sorted({path.parent for path in paths}, key=lambda path: len(path.parts), reverse=True):
        current = item
        while current not in protected:
            try:
                info = current.lstat()
            except FileNotFoundError:
                current = current.parent
                continue
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise LegacyLifecycleError("legacy source parent is unsafe")
            try:
                current.rmdir()
            except OSError:
                break
            current = current.parent


def delete_archived(project_root: Path, archive_id: str, confirmation: object) -> dict[str, Any]:
    if confirmation != f"DELETE_LEGACY_ARCHIVE:{archive_id}":
        raise LegacyLifecycleError("legacy deletion requires the exact archive-specific confirmation")
    root = project_root / ".codex" / "cortex"
    _, manifest = _read_archive_manifest(root, archive_id)
    scan = _scan(root)
    if scan["issues"]:
        raise LegacyLifecycleError("legacy deletion is blocked: " + "; ".join(scan["issues"]))
    indexed = {str(item["path"]): item for item in scan["files"]}
    removable: list[Path] = []
    for record in manifest["files"]:
        path_text = str(record["path"])
        current = indexed.get(path_text)
        if current is None:
            continue  # A prior interrupted delete may have removed this item.
        if current.get("sha256") != record.get("sha256") or int(current.get("size", -1)) != int(record.get("size", -2)):
            raise LegacyLifecycleError(f"legacy source changed after archive: {path_text}")
        removable.append(root / Path(path_text))
    for path in removable:
        _regular_file(path, "legacy deletion source")
    for path in removable:
        path.unlink()
    _remove_empty_parents(root, removable)
    return {
        "schema": SCHEMA,
        "ok": True,
        "outcome": "legacy_deleted",
        "archive_id": archive_id,
        "deleted_file_count": len(removable),
        "current_state_interpreted": False,
        "next_action": "Only archived, verified legacy filesystem sources were deleted; the archive and all current SQLite state were preserved.",
    }


def manage_legacy_lifecycle(params: dict[str, Any], project_root: Path) -> dict[str, Any]:
    """Route the explicit maintenance workflow without initializing the ledger."""
    payload = params.get("payload") if isinstance(params.get("payload"), dict) else {}
    unknown = sorted(set(payload) - {"action", "archive_id", "confirmation"})
    if unknown:
        raise LegacyLifecycleError("unsupported legacy payload fields: " + ", ".join(unknown))
    action = str(payload.get("action") or "inventory").strip().lower().replace("-", "_")
    if action in {"inventory", "audit", "list"}:
        if set(payload) - {"action"}:
            raise LegacyLifecycleError("legacy inventory accepts no archive or confirmation fields")
        return inventory(project_root)
    if action == "archive":
        if set(payload) - {"action"}:
            raise LegacyLifecycleError("legacy archive accepts no archive or confirmation fields")
        return archive(project_root)
    if action == "delete":
        archive_id = str(payload.get("archive_id") or "").strip()
        if not archive_id:
            raise LegacyLifecycleError("legacy deletion requires archive_id")
        return delete_archived(project_root, archive_id, payload.get("confirmation"))
    raise LegacyLifecycleError("legacy action must be inventory, archive, or delete")
