"""Runtime verification primitives shared with the candidate builder."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
from typing import Iterable, Mapping
import re


def normalized_payload(relative: str, payload: bytes) -> bytes:
    """Normalize only the generated plugin version field for identity hashing."""
    if relative != "plugins/cortex/.codex-plugin/plugin.json":
        return payload
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return payload
    if not isinstance(value, dict):
        return payload
    value["version"] = str(value.get("version", "")).split("+", 1)[0]
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def records_digest(records: Iterable[Mapping[str, object]]) -> str:
    encoded = json.dumps(
        {"files": list(records)}, ensure_ascii=False, separators=(",", ":"), sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def payload_digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def package_records(package_root: Path) -> tuple[dict[str, object], ...]:
    """Return records for every regular file in the installed plugin tree."""
    root = package_root.absolute()
    try:
        mode = root.lstat().st_mode
    except OSError as exc:
        raise RuntimeError("candidate package root is unreadable") from exc
    if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
        raise RuntimeError("candidate package root must be a regular directory")
    records: list[dict[str, object]] = []
    for base, directories, names in os.walk(root, followlinks=False):
        current = Path(base)
        for name in [*directories, *names]:
            path = current / name
            if path.is_symlink():
                raise RuntimeError("candidate package contains a symlink")
        for name in names:
            path = current / name
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode):
                raise RuntimeError("candidate package contains a non-regular file")
            relative = path.relative_to(root).as_posix()
            payload = normalized_payload(f"plugins/cortex/{relative}", path.read_bytes())
            records.append({
                "path": f"plugins/cortex/{relative}",
                "bytes": len(payload),
                "sha256": payload_digest(payload),
            })
    records.sort(key=lambda item: str(item["path"]))
    return tuple(records)


def package_digest(package_root: Path) -> str:
    return records_digest(package_records(package_root))


def verify_runtime(package_root: Path, server_version: str, environment: Mapping[str, str] | None = None, *, allow_source_mode: bool = False) -> dict[str, str]:
    """Verify a release, source, or content-addressed candidate package.

    Marketplace releases and isolated development candidates carry the same
    content digest suffix and must prove it against their installed bytes.
    Plain semantic versions are reserved for an explicitly enabled source
    checkout and never form an installable production package.
    """
    root = package_root.absolute()
    manifest_path = root / ".codex-plugin" / "plugin.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("candidate plugin manifest is unreadable") from exc
    if not isinstance(manifest, dict) or not isinstance(manifest.get("version"), str):
        raise RuntimeError("candidate plugin manifest is invalid")
    version = manifest["version"]
    match = re.fullmatch(r"(1\.14\.1)\+codex\.sha256\.([0-9a-f]{16})", version)
    # A source checkout keeps the last content-addressed release suffix in
    # version control. Explicit source mode must therefore tolerate that
    # suffix becoming stale while files are edited, while an installed or
    # candidate runtime still verifies it strictly.
    source_mode = allow_source_mode and (version == "1.14.1" or match is not None)
    if (match is None and not source_mode) or server_version != "1.14.1":
        raise RuntimeError("candidate product version or build suffix is invalid")
    digest = package_digest(root)
    if match is not None and not source_mode and not digest.startswith(match.group(2)):
        raise RuntimeError("candidate build suffix does not match package content")
    values = {
        "build_id": f"sha256:{digest}",
        "source_digest": digest,
        "candidate_path": str(root),
        "parity_verified": "true" if match is not None and not source_mode else "false",
        "runtime_mode": "source" if source_mode else "content_addressed",
    }
    supplied = environment if environment is not None else os.environ
    expected_build = supplied.get("CORTEX_BUILD_ID")
    expected_source = supplied.get("CORTEX_SOURCE_DIGEST")
    if expected_build and expected_build != values["build_id"]:
        raise RuntimeError("launcher build identity disagrees with candidate content")
    if expected_source and expected_source != values["source_digest"]:
        raise RuntimeError("launcher source digest disagrees with candidate content")
    return values
