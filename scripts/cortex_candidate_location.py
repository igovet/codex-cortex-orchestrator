#!/usr/bin/env python3
"""Canonical, fail-closed locations for Cortex qualification candidates.

Repository/release roots and native installed-plugin roots have different
topologies.  This module makes that distinction explicit so a receipt-selected
plugin root can never be treated as though it still contained ``plugins/cortex``.
"""
from __future__ import annotations

import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from cortex_payload_manifest import RuntimePayloadError, validated_managed_directory


class CandidateLocationError(RuntimeError):
    """A candidate root has the wrong topology or cannot safely be used."""


@dataclass(frozen=True)
class CandidateLocation:
    """One validated qualification target with paths derived exactly once."""

    kind: str
    plugin_root: Path
    server_path: Path
    runtime_package: Path
    release_root: Path | None = None


def _directory(value: str | Path, label: str) -> Path:
    raw = Path(value)
    if not raw.is_absolute() or any(part in {".", ".."} for part in raw.parts):
        raise CandidateLocationError(f"{label} must be an absolute lexical path")
    try:
        return validated_managed_directory(raw, label)
    except RuntimePayloadError as exc:
        raise CandidateLocationError(str(exc)) from None


def _regular(value: Path, label: str) -> Path:
    try:
        info = value.lstat()
    except OSError as exc:
        raise CandidateLocationError(f"{label} is missing or unreadable: {exc}") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise CandidateLocationError(f"{label} must be a regular non-symlink file")
    return value


def _from_plugin_root(*, kind: str, plugin_root: Path, release_root: Path | None) -> CandidateLocation:
    plugin = _directory(plugin_root, f"{kind} candidate plugin root")
    server = _regular(plugin / "scripts" / "cortex.py", f"{kind} candidate server")
    runtime = _directory(plugin / "scripts" / "cortex_runtime", f"{kind} candidate runtime package")
    _regular(plugin / ".codex-plugin" / "plugin.json", f"{kind} candidate plugin manifest")
    return CandidateLocation(
        kind=kind,
        plugin_root=plugin,
        server_path=server,
        runtime_package=runtime,
        release_root=release_root,
    )


def from_release_root(release_root: str | Path) -> CandidateLocation:
    """Resolve a checkout or complete staged-release root exactly once."""
    release = _directory(release_root, "candidate release root")
    # This constructor deliberately requires the release topology. Passing an
    # installed plugin root, or its nested ``plugins/cortex`` child, fails
    # instead of guessing which topology the caller intended.
    return _from_plugin_root(
        kind="release",
        plugin_root=release / "plugins" / "cortex",
        release_root=release,
    )


def from_verified_installed_receipt(
    receipt: Mapping[str, object], *, requested_root: str | Path,
) -> CandidateLocation:
    """Resolve only the plugin root named by an already verified receipt."""
    candidate = receipt.get("candidate_path")
    if not isinstance(candidate, str) or not candidate:
        raise CandidateLocationError("verified receipt has no candidate plugin root")
    requested = _directory(requested_root, "requested installed candidate root")
    if str(requested) != candidate:
        raise CandidateLocationError("requested installed root disagrees with verified receipt")
    # No child path is joined here: the receipt-selected path is already the
    # native plugin root. This is the single point that prevents the historical
    # `<installed>/plugins/cortex` duplicate nesting defect.
    return _from_plugin_root(kind="installed", plugin_root=requested, release_root=None)


__all__ = [
    "CandidateLocation", "CandidateLocationError", "from_release_root",
    "from_verified_installed_receipt",
]
