"""Worker-owned deterministic artifact observations.

This module is a worker procedure, never called by the ledger or MCP server.
It returns commitments, not file contents. Symlinks are hashed as links and
never followed outside the declared project boundary.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
from collections.abc import Mapping, Sequence
from typing import Any


MAX_FILES = 100_000
MAX_CONTENT_BYTES = 1_073_741_824
MAX_MANIFEST_BYTES = 64 * 1024 * 1024


class FingerprintError(ValueError):
    pass


def archive_path(codex_home: Path, project_hash: str) -> Path:
    """Host/project-separated worker scratch; never grant ledger write access.

    Ordinary workspace-write workers can write the system temporary directory,
    but cannot write the MCP process's private ledger tree. This only derives
    a path: the worker creates and validates the owner-private archive itself.
    """
    namespace = hashlib.sha256((str(codex_home) + "\0" + project_hash).encode()).hexdigest()
    return Path("/tmp").resolve() / f"cortex-artifacts-{os.getuid()}-{namespace}"


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def _relative(value: str) -> str:
    if not isinstance(value, str):
        raise FingerprintError("artifact path must be text")
    path = PurePosixPath(value)
    if not value or str(path) != value or path.is_absolute() or ".." in path.parts or ".git" in path.parts or "\x00" in value:
        raise FingerprintError("artifact path leaves the project boundary")
    return str(path)


def _entry(root: Path, relative: str, budget: list[int]) -> dict[str, Any]:
    path = root / relative
    # Reject a symlink in a parent path; resolving it would hash another tree.
    for parent in (() if path == root else path.parents):
        if parent == root:
            break
        if parent.is_symlink():
            raise FingerprintError("artifact parent is a symlink")
    try:
        before = path.lstat()
    except FileNotFoundError:
        return {"path": relative, "kind": "absent"}
    mode = stat.S_IMODE(before.st_mode)
    if stat.S_ISLNK(before.st_mode):
        return {"path": relative, "kind": "symlink", "mode": mode,
                "content": hashlib.sha256(os.fsencode(os.readlink(path))).hexdigest()}
    if stat.S_ISDIR(before.st_mode):
        return {"path": relative, "kind": "directory", "mode": mode}
    if not stat.S_ISREG(before.st_mode):
        raise FingerprintError("unsupported artifact file type")
    digest = hashlib.sha256()
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        opened = os.fstat(descriptor)
        if (before.st_dev, before.st_ino, before.st_mode) != (opened.st_dev, opened.st_ino, opened.st_mode) or not stat.S_ISREG(opened.st_mode):
            raise FingerprintError("artifact changed during observation")
        while True:
            chunk = os.read(descriptor, 1_048_576)
            if not chunk:
                break
            budget[0] += len(chunk)
            if budget[0] > MAX_CONTENT_BYTES:
                raise FingerprintError("artifact content bound exceeded")
            digest.update(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    last = path.lstat()
    signature = lambda item: (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns, item.st_ctime_ns, item.st_mode)
    if signature(before) != signature(after) or signature(after) != signature(last):
        raise FingerprintError("artifact changed during observation")
    return {"path": relative, "kind": "file", "mode": mode, "content": digest.hexdigest()}


def _git(root: Path, *arguments: str, allowed: tuple[int, ...] = (0,)) -> bytes:
    result = subprocess.run(["git", "-C", str(root), *arguments], stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, check=False, timeout=30,
                            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"})
    if result.returncode not in allowed:
        raise FingerprintError("Git artifact capability unavailable")
    return result.stdout


def _paths(root: Path, declared: Sequence[str]) -> list[str]:
    values: set[str] = set()
    for value in declared:
        relative = _relative(value)
        path = root / relative
        for parent in (() if path == root else path.parents):
            if parent == root:
                break
            if parent.is_symlink():
                raise FingerprintError("artifact parent is a symlink")
        if path.is_symlink() or not path.is_dir():
            values.add(relative)
        else:
            values.add(relative)
            for directory, folders, files in os.walk(path, followlinks=False):
                folders[:] = sorted(name for name in folders if name != ".git")
                for name in [*folders, *files]:
                    entry = Path(directory) / name
                    values.add(entry.relative_to(root).as_posix())
                    if len(values) > MAX_FILES:
                        raise FingerprintError("artifact file bound exceeded")
    if len(values) > MAX_FILES:
        raise FingerprintError("artifact file bound exceeded")
    return sorted(values)


def observe(root: Path, *, method: str, artifact_paths: Sequence[str]) -> dict[str, Any]:
    root = Path(root)
    if not root.is_absolute() or root.resolve() != root or not root.is_dir():
        raise FingerprintError("canonical existing project root required")
    if isinstance(artifact_paths, (str, bytes)) or not artifact_paths:
        raise FingerprintError("declared artifact boundary required")
    for path in artifact_paths:
        _relative(path)
    git_metadata = None
    if method == "git_content_v1":
        # Capability check precedes every Git-dependent observation.
        capability = _git(root, "rev-parse", "--is-inside-work-tree", allowed=(0, 128)).strip()
        if capability != b"true":
            raise FingerprintError("Git artifact capability unavailable")
        def metadata():
            index = _git(root, "ls-files", "--stage", "-z")
            index_entries: dict[str, list[str]] = {}
            for record in index.split(b"\x00"):
                if record:
                    entry, path = record.split(b"\t", 1)
                    index_entries.setdefault(_relative(os.fsdecode(path)), []).append(entry.decode("ascii"))
            return {
                "head": _git(root, "rev-parse", "--verify", "HEAD", allowed=(0, 128)).decode("ascii").strip() or "unborn",
                "index": hashlib.sha256(index).hexdigest(),
                "index_entries": index_entries,
                "paths": sorted({_relative(os.fsdecode(path)) for path in _git(root, "ls-files", "--cached", "--others", "--exclude-standard", "-z").split(b"\x00") if path} | set(_paths(root, artifact_paths))),
            }
        git_metadata = metadata()
        paths = git_metadata["paths"]
    elif method == "path_manifest_v1":
        paths = _paths(root, artifact_paths)
    else:
        raise FingerprintError("unsupported fingerprint method")
    if len(paths) > MAX_FILES:
        raise FingerprintError("artifact file bound exceeded")
    budget = [0]
    entries = [_entry(root, path, budget) for path in paths]
    if git_metadata is not None:
        for entry in entries:
            entry["index"] = git_metadata["index_entries"].get(entry["path"], [])
    if method == "git_content_v1":
        if metadata() != git_metadata:
            raise FingerprintError("Git state changed during observation")
    elif _paths(root, artifact_paths) != paths:
        raise FingerprintError("artifact set changed during observation")
    commitment = {"method": method, "paths": sorted(set(artifact_paths)), "entries": entries, "git": git_metadata}
    return {**commitment, "fingerprint": _digest(commitment),
            "content_bytes": budget[0]}


def validate_manifest(value: Any, *, expected_fingerprint: str | None = None) -> dict[str, Any]:
    """Verify a worker manifest commitment, not provenance or filesystem truth."""
    if not isinstance(value, dict) or set(value) != {"method", "paths", "entries", "git", "fingerprint", "content_bytes"}:
        raise FingerprintError("artifact manifest shape invalid")
    if value["method"] not in {"git_content_v1", "path_manifest_v1"}:
        raise FingerprintError("unsupported fingerprint method")
    if not isinstance(value["paths"], list) or not value["paths"] or any(not isinstance(path, str) for path in value["paths"]):
        raise FingerprintError("artifact manifest boundary invalid")
    if value["paths"] != sorted(set(_relative(path) for path in value["paths"])):
        raise FingerprintError("artifact manifest boundary invalid")
    if not isinstance(value["entries"], list) or len(value["entries"]) > MAX_FILES:
        raise FingerprintError("artifact manifest entries invalid")
    paths = []
    for entry in value["entries"]:
        if not isinstance(entry, dict) or "path" not in entry or entry.get("kind") not in {"absent", "file", "directory", "symlink"}:
            raise FingerprintError("artifact manifest entry invalid")
        paths.append(_relative(entry["path"]))
        required = {"path", "kind"}
        if entry["kind"] != "absent":
            required.add("mode")
            if type(entry.get("mode")) is not int or not 0 <= entry["mode"] <= 0o7777:
                raise FingerprintError("artifact manifest mode invalid")
        if entry["kind"] in {"file", "symlink"}:
            required.add("content")
            if not isinstance(entry.get("content"), str) or re.fullmatch(r"[0-9a-f]{64}", entry["content"]) is None:
                raise FingerprintError("artifact manifest content digest invalid")
        if value["method"] == "git_content_v1":
            required.add("index")
            if not isinstance(entry.get("index"), list) or any(not isinstance(item, str) or re.fullmatch(r"[0-7]{6} [0-9a-f]{40,64} [0-3]", item) is None for item in entry["index"]):
                raise FingerprintError("artifact manifest index entry invalid")
        if set(entry) != required:
            raise FingerprintError("artifact manifest entry shape invalid")
    if paths != sorted(set(paths)):
        raise FingerprintError("artifact manifest entries are not unique and sorted")
    if ((value["method"] == "path_manifest_v1" and value["git"] is not None)
            or (value["method"] == "git_content_v1" and not isinstance(value["git"], dict))):
        raise FingerprintError("artifact manifest Git state invalid")
    if value["git"] is not None:
        git = value["git"]
        if (set(git) != {"head", "index", "index_entries", "paths"}
                or not isinstance(git["head"], str) or re.fullmatch(r"(?:unborn|[0-9a-f]{40}|[0-9a-f]{64})", git["head"]) is None
                or not isinstance(git["index"], str) or re.fullmatch(r"[0-9a-f]{64}", git["index"]) is None
                or git["paths"] != paths
                or git["index_entries"] != {entry["path"]: entry["index"] for entry in value["entries"] if entry["index"]}):
            raise FingerprintError("artifact manifest Git state invalid")
    if type(value["content_bytes"]) is not int or not 0 <= value["content_bytes"] <= MAX_CONTENT_BYTES:
        raise FingerprintError("artifact manifest content bound invalid")
    commitment = {key: value[key] for key in ("method", "paths", "entries", "git")}
    if value["fingerprint"] != _digest(commitment) or (expected_fingerprint is not None and value["fingerprint"] != expected_fingerprint):
        raise FingerprintError("artifact manifest commitment mismatch")
    return value


def _archive_directory(project_root: Path, archive_root: Path) -> Path:
    project_root, archive_root = Path(project_root), Path(archive_root)
    if (not project_root.is_absolute() or project_root.resolve() != project_root
            or not project_root.is_dir() or not archive_root.is_absolute()
            or archive_root.resolve() != archive_root or archive_root.is_relative_to(project_root)):
        raise FingerprintError("artifact archive must be canonical and outside the project")
    archive_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    state = archive_root.lstat()
    if not stat.S_ISDIR(state.st_mode) or state.st_uid != os.getuid() or stat.S_IMODE(state.st_mode) != 0o700:
        raise FingerprintError("artifact archive must be owner-private")
    return archive_root


def save_manifest(project_root: Path, archive_root: Path, observation: Mapping[str, Any]) -> str:
    """Persist only hashed metadata, outside the mutable project, without overwrite."""
    import secrets
    value = validate_manifest(observation)
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    if len(payload) > MAX_MANIFEST_BYTES:
        raise FingerprintError("artifact manifest byte bound exceeded")
    archive = _archive_directory(project_root, archive_root)
    fingerprint = value["fingerprint"]
    path = archive / (fingerprint + ".json")
    temporary = archive / (".manifest-" + secrets.token_hex(16))
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path, follow_symlinks=False)
        except FileExistsError:
            load_manifest(project_root, archive, fingerprint)
    finally:
        temporary.unlink()
    return fingerprint


def load_manifest(project_root: Path, archive_root: Path, fingerprint: str) -> dict[str, Any]:
    if not isinstance(fingerprint, str) or re.fullmatch(r"[0-9a-f]{64}", fingerprint) is None:
        raise FingerprintError("artifact fingerprint invalid")
    archive = _archive_directory(project_root, archive_root)
    descriptor = os.open(archive / (fingerprint + ".json"), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        state = os.fstat(stream.fileno())
        if (not stat.S_ISREG(state.st_mode) or state.st_uid != os.getuid()
                or stat.S_IMODE(state.st_mode) != 0o600 or state.st_size > MAX_MANIFEST_BYTES):
            raise FingerprintError("artifact manifest file invalid")
        payload = stream.read(MAX_MANIFEST_BYTES + 1)
        if len(payload) > MAX_MANIFEST_BYTES:
            raise FingerprintError("artifact manifest byte bound exceeded")
    try:
        return validate_manifest(json.loads(payload), expected_fingerprint=fingerprint)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise FingerprintError("artifact manifest encoding invalid") from exc


def changed_paths(before: Mapping[str, Any], after: Mapping[str, Any], *, mutation_domains: Sequence[str]) -> dict[str, Any]:
    """Commit the whole changed set while exposing at most sixteen paths."""
    validate_manifest(before)
    validate_manifest(after)
    if before["method"] != after["method"] or before["paths"] != after["paths"]:
        raise FingerprintError("fingerprint method or boundary changed")
    domains = [_relative(value) for value in mutation_domains]
    old = {item["path"]: item for item in before["entries"]}
    new = {item["path"]: item for item in after["entries"]}
    changed = sorted(path for path in set(old) | set(new) if old.get(path) != new.get(path))
    head_changed = before["git"] is not None and before["git"].get("head") != after["git"].get("head")
    if head_changed:
        changed = sorted([*changed, ".git/HEAD"])
    within = all(any(domain == "." or path == domain or path.startswith(domain + "/") for domain in domains) for path in changed)
    return {"count": len(changed), "digest": _digest(changed), "samples": changed[:16], "within_domains": within and not head_changed}


def main() -> None:
    """Worker-only executable procedure; bounded output never includes manifests."""
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--archive-root", type=Path, required=True)
    parser.add_argument("--method", choices=("auto", "git_content_v1", "path_manifest_v1"), required=True)
    parser.add_argument("--artifact-path", action="append", required=True)
    parser.add_argument("--mutation-domain", action="append", default=[])
    parser.add_argument("--compare", action="append", default=[])
    args = parser.parse_args()
    try:
        if len(args.compare) > 2:
            raise FingerprintError("at most two comparison baselines are supported")
        method = args.method
        if method == "auto":
            # A missing Git installation or a non-Git project is a capability
            # fact, not a speculative command failure requiring model repair.
            try:
                supported = _git(args.project_root, "rev-parse", "--is-inside-work-tree", allowed=(0, 128)).strip() == b"true"
            except (OSError, subprocess.TimeoutExpired, FingerprintError):
                supported = False
            method = "git_content_v1" if supported else "path_manifest_v1"
        current = observe(args.project_root, method=method, artifact_paths=args.artifact_path)
        comparisons = [{"baseline": fingerprint, "changes": changed_paths(
            load_manifest(args.project_root, args.archive_root, fingerprint), current,
            mutation_domains=args.mutation_domain)} for fingerprint in args.compare]
        save_manifest(args.project_root, args.archive_root, current)
        reply = {"state": "observed", "method": method, "fingerprint": current["fingerprint"], "comparisons": comparisons}
        if len(comparisons) == 1:
            # Return the exact measured ordinary interval, not instructions or
            # caller-authored verification claims. No optional boundary or
            # reconciliation metadata can be inferred from this observation.
            reply["terminal_observation"] = {
                "method": method, "start": comparisons[0]["baseline"],
                "end": current["fingerprint"], "changes": comparisons[0]["changes"],
            }
    except (FingerprintError, OSError, subprocess.TimeoutExpired):
        # No exception/path/raw subprocess text is safe to expose here. This
        # is explicitly unavailable evidence, never a successful observation.
        reply = {"state": "unavailable", "reason": "Artifact observation or stored baseline could not be verified."}
    print(json.dumps(reply, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
