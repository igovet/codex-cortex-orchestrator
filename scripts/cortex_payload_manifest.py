"""Canonical installable Cortex Python-payload closure.

This module is intentionally dependency-free so the source candidate builder
and the candidate marketplace validator consume exactly the same closure
rules.  The runtime payload manifest is metadata; the actual source tree is
still authoritative, and must have exact declared/discovered parity.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import stat
from typing import Iterable


PLUGIN_ROOT = Path("plugins/cortex")
RUNTIME_MANIFEST = PLUGIN_ROOT / "runtime-payload.json"
RUNTIME_LAUNCHER = PLUGIN_ROOT / "scripts/cortex.py"
RUNTIME_PACKAGE = PLUGIN_ROOT / "scripts/cortex_runtime"


class RuntimePayloadError(RuntimeError):
    """The canonical runtime payload closure is incomplete or unsafe."""


@dataclass(frozen=True)
class RuntimePayloadClosure:
    """Repository-relative runtime files and their implied directories."""

    files: tuple[Path, ...]
    directories: tuple[Path, ...]


def validated_directory_root(path: Path, label: str, *, allow_missing: bool = False) -> Path:
    """Validate a directory with lstat before deriving an absolute path."""
    return validated_managed_directory(path, label, allow_missing=allow_missing)


def validated_managed_directory(path: Path, label: str, *, allow_missing: bool = False) -> Path:
    """Validate every lexical ancestor of a managed directory with ``lstat``.

    ``Path.resolve`` is deliberately not used: resolving first would hide a
    symlinked candidate/cache ancestor. Missing descendants are safe only when
    explicitly allowed; callers must create them and validate the resulting
    chain again before writing data below it.
    """
    raw = Path(path).expanduser()
    if any(part in {".", ".."} for part in raw.parts):
        raise RuntimePayloadError(f"{label} contains an unsafe path component")
    lexical = raw if raw.is_absolute() else Path.cwd() / raw
    current = Path(lexical.anchor)
    missing = False
    for part in lexical.parts[1:]:
        current /= part
        try:
            info = current.lstat()
        except FileNotFoundError:
            missing = True
            continue
        except OSError as exc:
            raise RuntimePayloadError(f"{label} is unreadable: {exc}") from exc
        if stat.S_ISLNK(info.st_mode):
            raise RuntimePayloadError(f"{label} must be a regular directory and must not traverse symlink: {current}")
        if not stat.S_ISDIR(info.st_mode):
            raise RuntimePayloadError(f"{label} must be a directory: {current}")
    if missing and not allow_missing:
        raise RuntimePayloadError(f"{label} is missing: {lexical}")
    return lexical


def ensure_managed_directory(path: Path, label: str) -> Path:
    """Create only missing managed directories below a trusted ancestor."""
    lexical = validated_managed_directory(path, label, allow_missing=True)
    current = Path(lexical.anchor)
    for part in lexical.parts[1:]:
        current /= part
        try:
            info = current.lstat()
        except FileNotFoundError:
            try:
                # Create one component at a time.  ``mkdir(parents=True)``
                # could traverse a concurrently substituted symlink in an
                # unvalidated intermediate component.
                current.mkdir(mode=0o700)
                info = current.lstat()
            except OSError as exc:
                raise RuntimePayloadError(f"{label} cannot be created: {exc}") from exc
        except OSError as exc:
            raise RuntimePayloadError(f"{label} is unreadable: {exc}") from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise RuntimePayloadError(f"{label} must be a directory and must not traverse symlink: {current}")
    return validated_managed_directory(lexical, label)


def validate_directory_topology(root: Path, declared_files: Iterable[Path], label: str) -> None:
    """Require exactly the directories implied by a regular-file manifest."""
    root = validated_managed_directory(root, label)
    expected = {
        parent for relative in declared_files
        for parent in Path(relative).parents
        if parent != Path(".")
    }
    actual: set[Path] = set()
    try:
        for base, directories, names in os.walk(root, followlinks=False):
            current = Path(base)
            for name in directories:
                path = current / name
                relative = path.relative_to(root)
                info = path.lstat()
                if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                    raise RuntimePayloadError(f"{label} contains an unsafe directory: {relative}")
                actual.add(relative)
            for name in names:
                path = current / name
                info = path.lstat()
                if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
                    raise RuntimePayloadError(f"{label} contains an unsafe file: {path.relative_to(root)}")
    except RuntimePayloadError:
        raise
    except OSError as exc:
        raise RuntimePayloadError(f"{label} topology is unreadable: {exc}") from exc
    if actual != expected:
        raise RuntimePayloadError(
            f"{label} directory topology is not exact; "
            f"missing={sorted(expected - actual)} extra={sorted(actual - expected)}"
        )


def _require_regular(path: Path, label: str) -> None:
    try:
        mode = path.lstat()
    except OSError as exc:
        raise RuntimePayloadError(f"{label} is missing or unreadable: {exc}") from exc
    if stat.S_ISLNK(mode.st_mode) or not stat.S_ISREG(mode.st_mode):
        raise RuntimePayloadError(f"{label} must be a regular file")


def _safe_manifest_path(raw: str) -> Path:
    candidate = PurePosixPath(raw.removeprefix("./"))
    if candidate.is_absolute() or not candidate.parts or any(part in {"", ".", ".."} for part in candidate.parts):
        raise RuntimePayloadError(f"runtime payload path is unsafe: {raw}")
    return Path(*candidate.parts)


def _tree_entries(root: Path) -> tuple[set[Path], set[Path]]:
    """Enumerate a regular tree while rejecting unsafe entries at every depth."""
    files: set[Path] = set()
    directories: set[Path] = set()
    try:
        for base, directories_in_base, filenames in os.walk(root, followlinks=False):
            current = Path(base)
            for name in filenames:
                path = current / name
                relative = path.relative_to(root)
                mode = path.lstat().st_mode
                if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
                    raise RuntimePayloadError(f"runtime payload contains unsafe file: {relative}")
                files.add(relative)
            for name in directories_in_base:
                path = current / name
                relative = path.relative_to(root)
                mode = path.lstat().st_mode
                if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
                    raise RuntimePayloadError(f"runtime payload contains unsafe directory: {relative}")
                directories.add(relative)
    except RuntimePayloadError:
        raise
    except OSError as exc:
        raise RuntimePayloadError(f"runtime payload tree is unreadable: {exc}") from exc
    return files, directories


def _implied_directories(files: Iterable[Path]) -> set[Path]:
    directories: set[Path] = set()
    for file in files:
        directories.update(parent for parent in file.parents if parent != Path("."))
    return directories


def runtime_payload_closure(repository_root: Path) -> RuntimePayloadClosure:
    """Validate and return the exact launcher/runtime Python closure."""
    root = validated_directory_root(repository_root, "repository root")
    plugin = validated_directory_root(root / PLUGIN_ROOT, "Cortex plugin root")
    runtime = validated_directory_root(root / RUNTIME_PACKAGE, "Cortex runtime package")
    _require_regular(root / RUNTIME_LAUNCHER, "Cortex runtime launcher")
    manifest_path = root / RUNTIME_MANIFEST
    _require_regular(manifest_path, "runtime payload manifest")
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimePayloadError(f"runtime payload manifest is invalid: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise RuntimePayloadError("runtime payload manifest has an unsupported schema version")
    raw_modules = payload.get("runtime_python")
    if not isinstance(raw_modules, list) or not raw_modules or not all(isinstance(item, str) for item in raw_modules):
        raise RuntimePayloadError("runtime payload manifest must declare runtime_python paths")
    if raw_modules != sorted(raw_modules):
        raise RuntimePayloadError("runtime payload manifest paths must be in canonical sorted order")
    declared: set[Path] = set()
    for raw in raw_modules:
        relative = _safe_manifest_path(raw)
        if relative == Path("scripts/cortex.py"):
            pass
        elif relative.parent == Path("scripts/cortex_runtime") or relative.is_relative_to(Path("scripts/cortex_runtime")):
            if relative.suffix != ".py":
                raise RuntimePayloadError(f"runtime payload path is not Python: {raw}")
        else:
            raise RuntimePayloadError(f"runtime payload path is outside the production Python closure: {raw}")
        full = PLUGIN_ROOT / relative
        if full in declared:
            raise RuntimePayloadError(f"runtime payload manifest contains a duplicate path: {raw}")
        _require_regular(root / full, f"runtime payload {raw}")
        declared.add(full)

    runtime_files, runtime_directories = _tree_entries(runtime)
    runtime_python = {path for path in runtime_files if path.suffix == ".py"}
    # Every nested runtime directory is a real Python package, never an
    # untracked implementation folder.  This also requires the root init.
    required_inits = {Path("__init__.py")}
    for path in runtime_python:
        required_inits.update(
            Path(*parent.parts, "__init__.py")
            for parent in path.parents
            if parent != Path(".")
        )
    missing_inits = sorted(required_inits - runtime_python)
    if missing_inits:
        raise RuntimePayloadError(f"runtime package is missing package initializers: {missing_inits}")
    actual = {RUNTIME_LAUNCHER, *(RUNTIME_PACKAGE / path for path in runtime_python)}
    if declared != actual:
        missing = sorted(actual - declared)
        extra = sorted(declared - actual)
        raise RuntimePayloadError(
            "runtime payload manifest is not an exact production Python closure; "
            f"missing={missing}; extra={extra}"
        )
    # Empty or otherwise undeclared runtime directories are never accepted.
    expected_runtime_dirs = {
        path for path in _implied_directories(actual)
        if path.is_relative_to(PLUGIN_ROOT) and path != PLUGIN_ROOT
    }
    actual_runtime_dirs = {
        RUNTIME_PACKAGE,
        *(RUNTIME_PACKAGE / path for path in runtime_directories),
        PLUGIN_ROOT / Path("scripts"),
    }
    if actual_runtime_dirs != expected_runtime_dirs:
        missing = sorted(expected_runtime_dirs - actual_runtime_dirs)
        extra = sorted(actual_runtime_dirs - expected_runtime_dirs)
        raise RuntimePayloadError(
            "runtime package directory topology is not exact; "
            f"missing={missing}; extra={extra}"
        )
    directories = _implied_directories(declared)
    # Keep the local variable as a root sanity check and make the package
    # boundary explicit to callers.
    if plugin != root / PLUGIN_ROOT:
        raise RuntimePayloadError("Cortex plugin root changed during payload validation")
    return RuntimePayloadClosure(tuple(sorted(declared)), tuple(sorted(directories)))


__all__ = [
    "RuntimePayloadClosure", "RuntimePayloadError", "ensure_managed_directory",
    "runtime_payload_closure", "validated_directory_root", "validated_managed_directory",
]
