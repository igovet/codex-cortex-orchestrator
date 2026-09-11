#!/usr/bin/env python3
"""Offline Phase 2 CLI control-record preparer.

This module deliberately does not start Codex, tmux, a provider, a listener, or
any evaluation workload.  It creates two private archive snapshots and seals
the condition-neutral controls needed by a later, separately authorized runner.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path
from typing import Any


SUITE = "phase2-cli-v1"
SCHEMA = "phase2-cli-v1-control-v2"
BASELINE_COMMIT = "17ace1ce2f7e3c5bb3dcf2b2b16424a16db7d7d9"
BASELINE_VERSION = "1.15.6+codex.sha256.cc786ae2fbd04cf1"
BASELINE_PAYLOAD_SHA256 = "cc786ae2fbd04cf1e9c29cfb34cf721de6ad6b8663f2d05f809baf2bee158698"
CANDIDATE_VERSION = "1.15.9+codex.sha256.e4f332d43bf38024"
CANDIDATE_PAYLOAD_SHA256 = "e4f332d43bf380248c5de142b835a62a888f8885ad6577677aa4f394804733d7"
TRUSTED_HARNESS_SHA256 = "9c625cf6d3aa895ef2895ebf969b8cf9cf1a27f5f70f10ee761b561d618e58b6"
TRUSTED_ADAPTER_SHA256 = "3a50a8d4892b7dd5ef6130d4a3b6114b3db8fa9d0116a1416d1ab1ff453e0328"
TRUSTED_OBSERVER_SHA256 = "2d0aff126189a013f41c7ff0ed2ae10e29386034b214e0e9afe9cc208efe75fb"
TRUSTED_OBSERVER_DEPENDENCIES_SHA256 = "2cd411e289b6e9e8850136d733ae2f873e1702838ad6aef30b7fe68b4b8dbe63"
EVIDENCE_COLLECTION = {
    "begin_command": "begin-cell",
    "cell_authorization_schema": "phase2-cli-cell-authorization-v2",
    "command": "collect-evidence",
    "schema_version": "phase2-cli-evidence-bundle-v1",
    "artifacts": ["status.txt", "terminal.json", "capture.txt", "events.jsonl", "calls.jsonl", "usage.json", "audit.json", "status-final.txt", "terminal-final.json", "capture-final.txt", "events-final.jsonl", "calls-final.jsonl", "audit-final.json", "evidence-bundle.json"],
    "bounds": {"capture_lines": 500, "events_lines": 500, "calls_limit": 10000},
    "directory_identity": ["canonical_path", "device", "inode", "owner_uid", "mode"],
    "session_lifecycle": {
        "order": ["start", "begin-cell", "send"],
        "start_completion": "ready-for-begin-after-trust-or-composer",
        "accept_trust_command": "idempotent-receipt-verification-only",
        "binding_schema": "phase2-cli-session-binding-v1",
        "receipt": "captured-once-after-owned-pane",
        "authorization_scope": ["control_record_sha256", "session_receipt"],
    },
    "terminal_proof": {
        "accepted_panes": ["successful-dead-bash", "idle-live-bash", "idle-live-codex-composer"],
        "marker": "required for bash; absent for idle composer",
        "stable_rechecks": ["status", "process-snapshot", "capture", "events", "calls", "audit"],
        "inactive_requirements": ["coordinator", "worker", "task", "exec", "wait", "child-process"],
    },
    "submission_transition": {
        "before_transport": "submitting",
        "successful_atomic_fields": ["status", "submission_request_sha256", "submitted_at", "submission_native_user_turn", "submission_receipt"],
        "uncertain_transport": "submitting",
        "reconciliation": "resolve-send-never-resends",
    },
    "stop_requires": "evidence-bundle.json",
    "orphan_recovery": {
        "command": "recover-orphan",
        "schema_version": "phase2-cli-orphan-recovery-v1",
        "eligible_authorization_states": ["issued", "submitting"],
        "captured_actions": ["status", "terminal", "capture", "calls", "events", "audit",
                             "status-final", "terminal-final", "capture-final"],
        "inactive_requirements": ["coordinator", "worker", "task", "exec", "wait"],
        "authorization": "explicit-one-time-nonce",
        "receipt": "orphan-recovery.json",
    },
    "pre_submit_abort": {
        "command": "abort-pre-submit",
        "schema_version": "phase2-cli-pre-submit-abort-v1",
        "requires": ["exact-control-session", "no-request-receipt", "no-submission-receipt",
                     "no-task-or-worker", "stable-owned-trust-prompt"],
        "cleanup": "one-ownership-revalidated-stop-exact-interrupt",
    },
    "invalid_cell_cleanup": {
        "command": "cleanup-invalid-cell",
        "schema_version": "phase2-cli-invalid-cell-cleanup-v1",
        "requires": ["exact-known-control", "submitted-receipt", "incomplete-evidence-receipt",
                     "stable-terminal-task-tree", "no-active-task-tool-worker-exec-or-wait"],
        "outcome": "invalidated-no-score-and-one-ownership-revalidated-stop-exact",
    },
    "absent_runtime_gc": {
        "command": "gc-absent-runtime",
        "schema_version": "phase2-cli-absent-runtime-gc-v1",
        "requires": ["tmux-server-session-pane-absent", "sealed-processes-gone",
                     "no-active-task-tool-workload", "explicit-empty-audit-open-state",
                     "single-saved-session", "canonical-state-root-allowlist",
                     "canonical-empty-or-absent-events-directory",
                     "exact-known-provenance",
                     "per-mutation-full-revalidation"],
        "pre_binding_failed_launch": {
            "schema_version": "phase2-cli-prebinding-failed-launch-gc-v1",
            "requires": ["matched-failed-control-workdir-transactions",
                         "matched-unusable-stopped-failure-receipts",
                         "starting-session-without-binding-authorization-submission-or-task",
                         "single-launcher-provenance-event", "empty-capture",
                         "canonical-private-paths"],
        },
        "mutation": "atomic-archive-exact-entries-with-durable-receipt-and-full-proof",
    },
}
HEX64 = set("0123456789abcdef")
REQUIRED_FAMILIES = {
    "F-01", "F-02", "F-03", "F-04", "F-05", "F-06",
}
REQUIRED_TOOLS = (
    "create_task", "set_governance", "create_draft", "read_draft",
    "write_report", "list_reports", "read_report",
)
UNSAFE_ENVIRONMENT_KEYS = (
    "PYTHONPATH", "PYTHONHOME", "CORTEX_DATA_DIR", "CODEX_HOME",
    "CORTEX_OBSERVATION_DIR", "CORTEX_DEPENDENCY_DIR",
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
    "http_proxy", "https_proxy", "all_proxy", "no_proxy",
    "LD_PRELOAD", "LD_AUDIT", "PYTHONINSPECT", "PYTHONSTARTUP",
    "PYTHONWARNINGS", "PYTHONPYCACHEPREFIX",
)
REQUIRED_MANIFEST_FIELDS = (
    "task_id", "family", "prompt_sha256", "fixture_tree_sha256",
    "oracle_sha256", "reset_sha256", "oracle_path", "reset_path",
    "reset_policy", "protected_paths", "oracle_command", "reset_command",
)


class PreparationError(ValueError):
    """A fail-closed preparation error suitable for a concise CLI diagnostic."""


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _is_hex64(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and set(value) <= HEX64


def _is_safe_relative_path(value: Any) -> bool:
    """Accept only a non-empty, slash-separated path inside a task tree."""
    if not isinstance(value, str) or not value or value != value.strip() or "\\" in value or "\x00" in value:
        return False
    path = Path(value)
    return not path.is_absolute() and all(part not in {"", ".", ".."} for part in path.parts)


def _validate_manifest_row(row: Any, expected_task: str) -> None:
    if not isinstance(row, dict):
        raise PreparationError("fixture manifest family row is not an object")
    for key in REQUIRED_MANIFEST_FIELDS:
        if key not in row:
            raise PreparationError(f"fixture manifest missing {key}")
    if row["task_id"] != expected_task:
        raise PreparationError(f"fixture manifest task mapping is invalid for {expected_task}")
    if not isinstance(row["family"], str) or not row["family"].strip():
        raise PreparationError(f"fixture manifest {expected_task} has invalid family")
    for key in ("prompt_sha256", "fixture_tree_sha256", "oracle_sha256", "reset_sha256"):
        if not _is_hex64(row[key]):
            raise PreparationError(f"fixture manifest has invalid {key}")
    # These paths and placeholders are part of the sealed reset/oracle contract;
    # accepting a remapped command would make the digest-only identity unusable.
    if row["oracle_path"] != "oracle.py" or row["reset_path"] != "reset.py":
        raise PreparationError(f"fixture manifest {expected_task} has remapped oracle/reset path")
    if row["oracle_command"] != "python3 oracle.py TASK_ID WORKTREE":
        raise PreparationError(f"fixture manifest {expected_task} has remapped oracle command")
    if row["reset_command"] != "python3 reset.py --task TASK_ID --destination WORKTREE":
        raise PreparationError(f"fixture manifest {expected_task} has remapped reset command")
    if not isinstance(row["reset_policy"], str) or not row["reset_policy"].strip():
        raise PreparationError("fixture reset contract is missing")
    policy = row["reset_policy"].casefold()
    if "absent" not in policy or "never overwrit" not in policy:
        raise PreparationError(f"fixture manifest {expected_task} has unsafe reset policy")
    paths = row["protected_paths"]
    if not isinstance(paths, list) or not paths or not all(_is_safe_relative_path(path) for path in paths) or "USER-NOTE.txt" not in paths:
        raise PreparationError(f"fixture manifest {expected_task} has invalid protected paths")


def _assert_real_owned_dir(path: Path, *, create: bool = False) -> None:
    """Reject symlinks and non-owner paths; optionally create mode 0700."""
    path = Path(path)
    if not path.is_absolute() or path.resolve() != path:
        raise PreparationError(f"path must be canonical absolute: {path}")
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if current.is_symlink():
            raise PreparationError(f"symlink path component: {current}")
    if create:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not path.exists() or not path.is_dir() or path.is_symlink():
        raise PreparationError(f"directory is not real: {path}")
    info = path.stat()
    if info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise PreparationError(f"directory is not owner-private: {path}")
    os.chmod(path, 0o700)


def _assert_real_parent(path: Path) -> None:
    """Validate a parent without requiring shared temp roots to be private."""
    if not path.is_absolute() or path.resolve() != path or path.is_symlink() or not path.is_dir():
        raise PreparationError(f"output parent is not a real canonical directory: {path}")


def _assert_real_source_dir(path: Path) -> None:
    """Validate a source checkout without requiring the user's checkout to be private."""
    if not path.is_absolute() or path.resolve() != path or path.is_symlink() or not path.is_dir():
        raise PreparationError(f"source is not a real canonical directory: {path}")
    if path.stat().st_uid != os.getuid():
        raise PreparationError(f"source is not owned by the invoking user: {path}")


def _assert_no_symlinks(path: Path) -> None:
    for parent, directories, files in os.walk(path, followlinks=False):
        for name in (*directories, *files):
            entry = Path(parent) / name
            if entry.is_symlink():
                raise PreparationError(f"symlink in source snapshot: {entry}")


def _canonical_source(path: Path) -> Path:
    path = Path(path)
    _assert_real_source_dir(path)
    _assert_no_symlinks(path)
    probe = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=path,
        capture_output=True,
        text=True,
        check=False,
    )
    if probe.returncode != 0:
        raise PreparationError("source is not a trusted Git worktree")
    root = Path(probe.stdout.strip())
    if not root.is_absolute() or root.resolve() != root or root != path:
        raise PreparationError("source Git root is not the supplied canonical directory")
    return root


def _run_git(source: Path, args: list[str], *, capture: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(["git", *args], cwd=source, capture_output=capture, text=True, check=False)
    if result.returncode != 0:
        detail = (result.stderr or "").strip()[:240]
        raise PreparationError(f"git {' '.join(args[:2])} failed: {detail}")
    return result


def _payload_digest(plugin: Path, *, canonical_version: str) -> str:
    if not plugin.is_dir() or plugin.is_symlink():
        raise PreparationError(f"missing payload directory: {plugin}")
    _assert_no_symlinks(plugin)
    digest = hashlib.sha256()
    for path in sorted(plugin.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(plugin).as_posix()
        if path.suffix in {".pyc", ".pyo"} or "__pycache__" in path.parts:
            raise PreparationError("bytecode is not allowed in a payload")
        body = path.read_bytes()
        if rel == ".codex-plugin/plugin.json":
            try:
                value = json.loads(body)
                value["version"] = canonical_version
                body = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
            except (UnicodeDecodeError, json.JSONDecodeError, KeyError) as exc:
                raise PreparationError("invalid plugin identity file") from exc
        digest.update(rel.encode() + b"\0" + str(len(body)).encode() + b"\0" + body)
    return digest.hexdigest()


def _payload_version(plugin: Path) -> str:
    manifest = plugin / ".codex-plugin" / "plugin.json"
    if manifest.is_symlink() or not manifest.is_file():
        raise PreparationError("missing plugin identity file")
    try:
        value = json.loads(manifest.read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PreparationError("invalid plugin identity file") from exc
    version = value.get("version") if isinstance(value, dict) else None
    if not isinstance(version, str):
        raise PreparationError("invalid plugin identity version")
    return version


def _validate_payload_identity(plugin: Path, *, expected_version: str, expected_digest: str) -> str:
    """Bind the normalized package digest to the exact embedded archive version."""
    if _payload_version(plugin) != expected_version:
        raise PreparationError("archived payload version does not match locked identity")
    canonical_version = expected_version.split("+", 1)[0]
    actual_digest = _payload_digest(plugin, canonical_version=canonical_version)
    if actual_digest != expected_digest:
        raise PreparationError("archived payload bytes do not match locked identity")
    return actual_digest


def _archive_snapshot(source: Path, revision: str, destination: Path) -> None:
    """Create a detached archive snapshot without adding a Git worktree."""
    raw = subprocess.run(
        ["git", "archive", "--format=tar", revision],
        cwd=source,
        capture_output=True,
        check=False,
    )
    if raw.returncode != 0:
        raise PreparationError(f"cannot archive revision {revision}")
    destination.mkdir(mode=0o700)
    with tarfile.open(fileobj=io.BytesIO(raw.stdout), mode="r:") as archive:
        for member in archive.getmembers():
            name = Path(member.name)
            if name.is_absolute() or ".." in name.parts:
                raise PreparationError("unsafe path in Git archive")
            target = destination / name
            if member.issym() or member.islnk():
                raise PreparationError("symlink in Git archive")
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True, mode=0o700)
            elif member.isfile():
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                source_file = archive.extractfile(member)
                if source_file is None:
                    raise PreparationError("unreadable Git archive member")
                target.write_bytes(source_file.read())
                # Preserve Git's executable semantics while removing all group
                # and other access. The top-level launcher remains 0600 and is
                # invoked by the common harness through explicit Bash.
                archived_mode = 0o700 if member.mode & 0o111 else 0o600
                if name.as_posix() == "scripts/cortex-dev":
                    archived_mode = 0o600
                os.chmod(target, archived_mode)
            else:
                raise PreparationError("unsupported Git archive member")
    _assert_no_symlinks(destination)


def _copy_candidate_payload(source: Path, destination: Path) -> None:
    payload = source / "plugins" / "cortex"
    _assert_no_symlinks(payload)
    target = destination / "plugins" / "cortex"
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if target.exists() or target.is_symlink():
        if target.is_symlink() or not target.is_dir():
            raise PreparationError("candidate archive payload target is unsafe")
        shutil.rmtree(target)
    shutil.copytree(payload, target, symlinks=False)
    _assert_no_symlinks(target)


def _validate_manifest(path: Path) -> tuple[dict[str, Any], str]:
    if not path.is_file() or path.is_symlink():
        raise PreparationError("fixture manifest is not a regular file")
    try:
        manifest = json.loads(path.read_bytes())
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PreparationError("fixture manifest is not valid JSON") from exc
    if not isinstance(manifest, dict) or manifest.get("suite_version") != SUITE:
        raise PreparationError("fixture manifest suite mismatch")
    families = manifest.get("families")
    if not isinstance(families, list) or len(families) != 6 or {row.get("task_id") for row in families if isinstance(row, dict)} != REQUIRED_FAMILIES:
        raise PreparationError("fixture manifest must contain exactly F-01 through F-06")
    for index, row in enumerate(families, start=1):
        _validate_manifest_row(row, f"F-{index:02d}")
    return manifest, _sha256_file(path)


def _tool_receipt(name: str, command: list[str]) -> dict[str, Any]:
    executable = shutil.which(command[0])
    if executable is None:
        return {"status": "unavailable", "reason": f"{command[0]} is not installed", "version": None}
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return {"status": "unavailable", "reason": f"{name} version probe exited {result.returncode}", "version": None}
    version = (result.stdout or result.stderr).strip().splitlines()[0][:160]
    return {"status": "observed", "reason": None, "version": version}


def _unavailable_tool_receipt(name: str) -> dict[str, Any]:
    """Describe an unavailable live host without probing or invoking it."""
    return {
        "status": "unavailable",
        "reason": f"offline preparation does not invoke {name}",
        "version": None,
    }


def _environment_receipt(allowlist: Path, proxy_policy: str) -> dict[str, Any]:
    if proxy_policy != "blocked":
        raise PreparationError("proxy policy must be explicitly blocked")
    allowlist_entries = []
    for raw_line in allowlist.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", line) or line in allowlist_entries:
            raise PreparationError("environment allowlist must contain unique variable names")
        if line in UNSAFE_ENVIRONMENT_KEYS:
            raise PreparationError(f"environment allowlist may not contain denied key: {line}")
        allowlist_entries.append(line)
    if not allowlist_entries:
        raise PreparationError("environment allowlist is empty")
    present = sorted(
        key for key in os.environ
        if key in UNSAFE_ENVIRONMENT_KEYS or key.casefold().endswith("_proxy")
    )
    if present:
        raise PreparationError("ambient environment keys must be unset: " + ", ".join(present))
    previous_umask = os.umask(0o077)
    return {
        "allowlist_sha256": _sha256_file(allowlist),
        "allowlist_keys": allowlist_entries,
        "ambient_paths": "unset",
        "proxy_policy": proxy_policy,
        "umask": format(0o077, "03o") if previous_umask != 0o077 else "077",
        "unsafe_environment_keys": {key: "absent" for key in UNSAFE_ENVIRONMENT_KEYS},
    }


def _require_file(path: Path, label: str) -> Path:
    if not path.is_absolute() or path.resolve() != path or not path.is_file() or path.is_symlink():
        raise PreparationError(f"{label} must be a canonical regular file")
    return path


def _observer_dependency_manifest(source: Path) -> dict[str, str]:
    """Return the complete dynamic file closure used by the common observer."""
    profiles = _require_file(source / "plugins/cortex/profiles.json", "observer profiles dependency")
    skills = source / "plugins/cortex/skills"
    if not skills.is_dir() or skills.is_symlink():
        raise PreparationError("observer skills dependency must be a real directory")
    paths = [profiles]
    for path in sorted(skills.rglob("*")):
        if path.is_symlink():
            raise PreparationError("observer dependency closure contains a symlink")
        if path.is_file():
            paths.append(path)
    manifest = {path.relative_to(source).as_posix(): _sha256_file(path) for path in paths}
    digest = _sha256_bytes(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode())
    if digest != TRUSTED_OBSERVER_DEPENDENCIES_SHA256:
        raise PreparationError("trusted common observer dependency identity mismatch")
    return manifest


def _copy_observer_dependencies(source: Path, destination: Path) -> dict[str, str]:
    manifest = _observer_dependency_manifest(source)
    for relative, digest in manifest.items():
        target = destination / relative
        _assert_real_owned_dir(target.parent, create=True)
        shutil.copyfile(source / relative, target)
        os.chmod(target, 0o600)
        if _sha256_file(target) != digest:
            raise PreparationError("common observer dependency copy mismatch")
    return manifest


def _dependency_receipt(lock: Path) -> dict[str, Any]:
    body = lock.read_text(encoding="utf-8")
    entries = [line.strip() for line in body.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    if not entries:
        raise PreparationError("dependency lock is empty")
    for line in entries:
        fields = line.split()
        if "==" not in fields[0] or not any(field.startswith("--hash=sha256:") and _is_hex64(field.split(":", 1)[1]) for field in fields[1:]):
            raise PreparationError("dependency lock must contain pinned hash-locked entries")
    return {
        "route": "external-hash-locked-file",
        "lock_sha256": _sha256_file(lock),
        "installed_tree_sha256": {"status": "unavailable", "sha256": None, "reason": "offline preparation performs no installation"},
        "combined_dependency_sha256": _sha256_bytes(("phase2-dependency-v1\0" + _sha256_file(lock)).encode()),
    }


def _opaque_receipt(kind: str) -> dict[str, Any]:
    return {
        "schema": kind,
        "status": "not_started",
        "exit_code": None,
        "open_receipt": False,
        "duplicate_receipt": False,
        "normalization": "normalized-preparation-v1",
        "reason": "offline preparer did not invoke a live workload",
    }


def _control_fingerprint(value: dict[str, Any]) -> str:
    return _sha256_bytes(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())


def prepare(args: argparse.Namespace) -> dict[str, Any]:
    source = _canonical_source(Path(args.source_repo))
    candidate_source = _canonical_source(Path(args.candidate_source))
    if candidate_source != source:
        raise PreparationError("candidate source must be the same canonical checkout as source")
    manifest_path = _require_file(Path(args.manifest), "fixture manifest")
    lock_path = _require_file(Path(args.dependency_lock), "dependency lock")
    allowlist_path = _require_file(Path(args.environment_allowlist), "environment allowlist")
    if not args.provider_route.startswith("offline://"):
        raise PreparationError("provider route must be an explicit offline:// contract")
    for name, value in (("candidate_version", args.candidate_version), ("baseline_version", args.baseline_version)):
        if not value.strip():
            raise PreparationError(f"{name} must be explicit")
    if args.candidate_payload_sha256 != CANDIDATE_PAYLOAD_SHA256 or args.baseline_payload_sha256 != BASELINE_PAYLOAD_SHA256:
        raise PreparationError("supplied payload identity does not match the locked Phase 2 identities")
    if args.baseline_commit != BASELINE_COMMIT or args.baseline_version != BASELINE_VERSION or args.candidate_version != CANDIDATE_VERSION:
        raise PreparationError("supplied version or baseline commit does not match the locked identity")
    if args.coordinator_model != "gpt-5.6-luna" or args.coordinator_effort != "high" or args.worker_model != "gpt-5.6-luna" or args.worker_effort not in {"medium", "high"}:
        raise PreparationError("model/effort contract is not Luna-only with coordinator high effort")
    if args.cell_timeout_seconds <= 0 or args.wait_timeout_ms <= 0:
        raise PreparationError("timeouts must be positive")
    manifest, manifest_sha = _validate_manifest(manifest_path)
    dependency = _dependency_receipt(lock_path)
    environment = _environment_receipt(allowlist_path, args.proxy_policy)
    _run_git(source, ["cat-file", "-e", f"{args.baseline_commit}^{{commit}}"])
    candidate_source_commit = _run_git(candidate_source, ["rev-parse", "HEAD"]).stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{40}", candidate_source_commit):
        raise PreparationError("candidate source HEAD is not a canonical commit")
    destination = Path(args.output)
    if not destination.is_absolute() or destination.resolve() != destination:
        raise PreparationError("output must be canonical absolute")
    if destination.exists():
        raise PreparationError("output already exists; use a new disposable directory")
    parent = destination.parent
    _assert_real_parent(parent)
    _assert_real_owned_dir(destination, create=True)
    try:
        baseline_dir = destination / "arms" / "baseline"
        candidate_dir = destination / "arms" / "candidate"
        harness_dir = destination / "harness"
        _assert_real_owned_dir(baseline_dir.parent, create=True)
        _archive_snapshot(source, args.baseline_commit, baseline_dir)
        _archive_snapshot(source, "HEAD", candidate_dir)
        _copy_candidate_payload(candidate_source, candidate_dir)
        baseline_digest = _validate_payload_identity(
            baseline_dir / "plugins" / "cortex",
            expected_version=args.baseline_version,
            expected_digest=args.baseline_payload_sha256,
        )
        candidate_digest = _validate_payload_identity(
            candidate_dir / "plugins" / "cortex",
            expected_version=args.candidate_version,
            expected_digest=args.candidate_payload_sha256,
        )
        _assert_real_owned_dir(harness_dir, create=True)
        harness_source = _require_file(candidate_source / "scripts" / "cortex-live-smoke", "common live harness")
        adapter_source = _require_file(candidate_source / "scripts" / "phase2_cli_adapter.py", "Phase 2 live adapter")
        observer_source = _require_file(candidate_source / "scripts" / "cortex-desktop-dev", "common live observer")
        if (_sha256_file(harness_source) != TRUSTED_HARNESS_SHA256
                or _sha256_file(observer_source) != TRUSTED_OBSERVER_SHA256
                or _sha256_file(adapter_source) != TRUSTED_ADAPTER_SHA256):
            raise PreparationError("trusted common live-launcher identity mismatch")
        harness_path = harness_dir / "cortex-live-smoke"
        shutil.copyfile(harness_source, harness_path)
        os.chmod(harness_path, 0o600)
        observer_path = harness_dir / "cortex-desktop-dev"
        shutil.copyfile(observer_source, observer_path)
        os.chmod(observer_path, 0o600)
        observer_dependencies = _copy_observer_dependencies(candidate_source, destination)
        for arm in (baseline_dir, candidate_dir):
            cortex = arm / ".codex" / "cortex"
            _assert_real_owned_dir(cortex, create=True)
            store = cortex / "cortex.sqlite3"
            if store.exists() or store.is_symlink():
                raise PreparationError("fresh store is not absent")
        host = {
            "host_id_sha256": _sha256_bytes((platform.node() + "\0" + platform.machine()).encode()),
            "python": {"status": "observed", "version": platform.python_version(), "executable_sha256": _sha256_file(Path(sys.executable))},
            "git": _tool_receipt("git", ["git", "--version"]),
            "tmux": _unavailable_tool_receipt("tmux"),
            "codex": _unavailable_tool_receipt("codex"),
        }
        tools = {
            "mcp_names": list(REQUIRED_TOOLS),
            "apps": {"status": "unavailable", "reason": "offline preparer has no app host"},
            "codebase_memory": {"status": "disabled", "reason": "diagnostic excluded from quality"},
            "node_repl": {"status": "unavailable", "reason": "offline preparer has no Node host"},
            "capabilities_sha256": _sha256_bytes(json.dumps({"mcp_names": REQUIRED_TOOLS, "apps": False, "codebase_memory": False, "node_repl": False}, sort_keys=True, separators=(",", ":")).encode()),
        }
        neutral = {
            "suite": SUITE,
            "host": host,
            "evaluator": {
                "runner_sha256": _sha256_file(Path(__file__).resolve()),
                "auditor_sha256": _sha256_file(Path(__file__).with_name("phase2_cli_auditor.py")),
                "preparer_sha256": _sha256_file(Path(__file__).resolve()),
            },
            "live_launcher": {
                "mechanism": "phase2-common-live-harness-v1",
                "adapter_path": str(adapter_source),
                "adapter_sha256": TRUSTED_ADAPTER_SHA256,
                "harness_path": str(harness_path),
                "harness_sha256": TRUSTED_HARNESS_SHA256,
                "observer_path": str(observer_path),
                "observer_sha256": TRUSTED_OBSERVER_SHA256,
                "observer_bundle_root": str(destination),
                "observer_dependencies": observer_dependencies,
                "observer_dependencies_sha256": TRUSTED_OBSERVER_DEPENDENCIES_SHA256,
                "fresh_store": "required-at-start",
                "model": args.coordinator_model,
                "effort": args.coordinator_effort,
                "evidence_collection": EVIDENCE_COLLECTION,
            },
            "interpreter_dependencies": {**dependency, "python_executable_sha256": host["python"]["executable_sha256"]},
            "model": {
                "coordinator": args.coordinator_model,
                "coordinator_effort": args.coordinator_effort,
                "worker": args.worker_model,
                "worker_effort": args.worker_effort,
                "consultant": "disabled",
            },
            "provider": {"route": args.provider_route, "route_sha256": _sha256_bytes(args.provider_route.encode()), "status": "declared-common-offline-contract"},
            "tools": tools,
            "task": {
                "manifest_sha256": manifest_sha,
                "prompt_sha256_by_family": {row["task_id"]: row["prompt_sha256"] for row in manifest["families"]},
                "fixture_tree_sha256_by_family": {row["task_id"]: row["fixture_tree_sha256"] for row in manifest["families"]},
                "oracle_sha256_by_family": {row["task_id"]: row["oracle_sha256"] for row in manifest["families"]},
                "reset_sha256_by_family": {row["task_id"]: row["reset_sha256"] for row in manifest["families"]},
                "fixture_access": "manifest-only; fixture tree was not read or written",
            },
            "timeout_wait": {
                "submit_delay_seconds": 5,
                "submit_receipt_poll": "40 x 250ms",
                "cell_timeout_seconds": args.cell_timeout_seconds,
                "wait_timeout_ms": args.wait_timeout_ms,
                "timeout_is_terminal": False,
            },
            "diagnostics": {
                "codebase_memory": "disabled",
                "git_rg": "metadata-only",
                "network_dependent_oracle": False,
                "private_cortex_mcp_lifecycle_transport": "hard-stop",
                "raw_logs": "not collected",
            },
            "environment": environment,
            "evidence_schema": {
                "control": SCHEMA,
                "terminal": "phase2-terminal-v1",
                "audit": "phase2-audit-v1",
                "opaque_run_id": "required later; not allocated offline",
                "quality_separate_from_diagnostics": True,
            },
        }
        arms = []
        for arm_name, arm_dir, version, payload_digest in (
            ("baseline", baseline_dir, args.baseline_version, baseline_digest),
            ("candidate", candidate_dir, args.candidate_version, candidate_digest),
        ):
            transport_files = {}
            for name, relative, expected_mode in (("launcher", "scripts/cortex-dev", 0o600), ("observer", "scripts/cortex-desktop-dev", 0o700)):
                path = arm_dir / relative
                info = path.stat()
                if path.is_symlink() or not path.is_file() or info.st_uid != os.getuid() or info.st_mode & 0o777 != expected_mode:
                    raise PreparationError(f"{arm_name} archived {name} has an unsafe mode or identity")
                transport_files[name] = {
                    "relative_path": relative,
                    "sha256": _sha256_file(path),
                    "mode": f"{expected_mode:04o}",
                }
            receipt = {
                "arm": arm_name,
                "identity": {
                    "version": version,
                    "baseline_commit": args.baseline_commit if arm_name == "baseline" else None,
                    "source_commit": args.baseline_commit if arm_name == "baseline" else candidate_source_commit,
                    "payload_sha256": payload_digest,
                },
                "workdir": str(arm_dir),
                "workdir_sha256": _sha256_bytes(str(arm_dir).encode()),
                "checkout": "detached Git archive snapshot; candidate payload overlaid from supplied worktree" if arm_name == "candidate" else "detached Git archive snapshot",
                "fresh_store": {"status": "observed_absent", "relative_path": ".codex/cortex/cortex.sqlite3", "sha256": None},
                "reset": {"status": "contract_sealed", "manifest_sha256": manifest_sha, "reason": "fixture reset is supplied by the immutable manifest and is not invoked by this offline preparer"},
                "terminal_receipt": _opaque_receipt("phase2-terminal-v1"),
                "audit_receipt": _opaque_receipt("phase2-audit-v1"),
                "transport_files": transport_files,
                "live_launcher_mechanism": "phase2-common-live-harness-v1",
                "control_neutral_fingerprint": _control_fingerprint(neutral),
            }
            arms.append(receipt)
        record = {
            "schema_version": SCHEMA,
            "suite": SUITE,
            "status": "prepared-offline",
            "manifest_path": str(manifest_path),
            "neutral": neutral,
            "identities": {
                "baseline": {"commit": args.baseline_commit, "version": args.baseline_version, "payload_sha256": args.baseline_payload_sha256},
                "candidate": {"version": args.candidate_version, "source_commit": candidate_source_commit, "payload_sha256": args.candidate_payload_sha256},
            },
            "arms": arms,
            "live_activity": {"status": "none", "codex_invoked": False, "listener_touched": False, "network_used": False, "score_or_reveal": False},
        }
        output_file = destination / "control-record.json"
        output_file.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.chmod(output_file, 0o400)
        return record
    except Exception:
        shutil.rmtree(destination, ignore_errors=False)
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-repo", required=True)
    parser.add_argument("--candidate-source", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--dependency-lock", required=True)
    parser.add_argument("--environment-allowlist", required=True)
    parser.add_argument("--provider-route", required=True)
    parser.add_argument("--proxy-policy", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--baseline-commit", required=True)
    parser.add_argument("--baseline-version", required=True)
    parser.add_argument("--baseline-payload-sha256", required=True)
    parser.add_argument("--candidate-version", required=True)
    parser.add_argument("--candidate-payload-sha256", required=True)
    parser.add_argument("--coordinator-model", required=True)
    parser.add_argument("--coordinator-effort", required=True)
    parser.add_argument("--worker-model", required=True)
    parser.add_argument("--worker-effort", required=True)
    parser.add_argument("--cell-timeout-seconds", required=True, type=int)
    parser.add_argument("--wait-timeout-ms", required=True, type=int)
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        parsed = build_parser().parse_args(argv)
        record = prepare(parsed)
        print(json.dumps({"status": record["status"], "control_record": str(Path(parsed.output) / "control-record.json"), "suite": SUITE}, sort_keys=True))
        return 0
    except (PreparationError, OSError, subprocess.SubprocessError) as exc:
        print(f"phase2-cli-v1 preparation refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
