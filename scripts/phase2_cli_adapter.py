#!/usr/bin/env python3
"""Fail-closed compatibility adapter for frozen Phase 2 CLI arm snapshots.

The archived product trees intentionally remain byte-for-byte unchanged.  This
adapter loads one common, hash-sealed transport harness and binds its repository
root to the selected archived arm before dispatch.  Both arms therefore use the
same fresh-store and model/effort implementation even when their historical
``cortex-live-smoke`` parsers expose different options.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import math
import os
import re
import runpy
import secrets
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


SCHEMA = "phase2-cli-v1-control-v2"
MECHANISM = "phase2-common-live-harness-v1"
MODEL = "gpt-5.6-luna"
EFFORT = "high"
TRUSTED_HARNESS_SHA256 = "9c625cf6d3aa895ef2895ebf969b8cf9cf1a27f5f70f10ee761b561d618e58b6"
TRUSTED_OBSERVER_SHA256 = "2d0aff126189a013f41c7ff0ed2ae10e29386034b214e0e9afe9cc208efe75fb"
TRUSTED_OBSERVER_DEPENDENCIES_SHA256 = "2cd411e289b6e9e8850136d733ae2f873e1702838ad6aef30b7fe68b4b8dbe63"
BASELINE_VERSION = "1.15.6+codex.sha256.cc786ae2fbd04cf1"
BASELINE_PAYLOAD_SHA256 = "cc786ae2fbd04cf1e9c29cfb34cf721de6ad6b8663f2d05f809baf2bee158698"
CANDIDATE_VERSION = "1.15.9+codex.sha256.e4f332d43bf38024"
CANDIDATE_PAYLOAD_SHA256 = "e4f332d43bf380248c5de142b835a62a888f8885ad6577677aa4f394804733d7"
EVIDENCE_SCHEMA = "phase2-cli-evidence-bundle-v1"
CELL_AUTH_SCHEMA = "phase2-cli-cell-authorization-v2"
SESSION_BINDING_SCHEMA = "phase2-cli-session-binding-v1"
ORPHAN_RECOVERY_SCHEMA = "phase2-cli-orphan-recovery-v1"
PRE_SUBMIT_ABORT_SCHEMA = "phase2-cli-pre-submit-abort-v1"
INVALID_CELL_CLEANUP_SCHEMA = "phase2-cli-invalid-cell-cleanup-v1"
LEGACY_INVALID_CONTROL_SHA256 = "29093def029c9b741fb90cc12f2d64c7a0c96b7b3453d1887af194a7a7e0f44b"
LEGACY_INVALID_HARNESS_SHA256 = "8dff4a2a743c37e721ef0d389264e68ad69a7233f25b2114a5663896f208c05d"
LEGACY_INVALID_ADAPTER_SHA256 = "ce9e405971cd3b62c2a423512734333f0a29b0b160da7a0b227c7164ac65c8f1"
MISSING_PROFILES_INVALID_CONTROL_SHA256 = "49a415337201286bbadf411275d6a82dba3031d284c886431f135d1a45ac7a3f"
MISSING_PROFILES_INVALID_ADAPTER_SHA256 = "b26a42e7bf13e0befe2d9d88db3368449172b13137216666ad2395590882f583"
INCOMPLETE_AUDIT_INVALID_CONTROL_SHA256 = "e2afa9955a3e5c2887d872cf456d6a2880a1d6785f4cefae57a12549928baedb"
ABSENT_RUNTIME_GC_SCHEMA = "phase2-cli-absent-runtime-gc-v1"
PREBINDING_FAILED_LAUNCH_GC_SCHEMA = "phase2-cli-prebinding-failed-launch-gc-v1"
LAUNCH_TRANSACTION_SCHEMA = "phase2-cli-launch-transaction-v1"
POST_COMMIT_FAILURE_SCHEMA = "phase2-cli-post-commit-launch-failure-v1"
TERMINAL_CALL_OUTCOMES = {"success", "error", "covered_by_nested", "covered_by_command_execution"}
TERMINAL_HOST_STATUSES = {None, "completed"}
REPORT_ID_PATTERN = re.compile(r"r_[0-9a-f]{12}")
DRAFT_ID_PATTERN = re.compile(r"d_[0-9a-f]{12}")
INVALID_CELL_CLEANUP_INCIDENTS = {
    LEGACY_INVALID_CONTROL_SHA256: {
        "adapter_sha256": LEGACY_INVALID_ADAPTER_SHA256,
        "harness_sha256": LEGACY_INVALID_HARNESS_SHA256,
        "completed_actions": ["status", "terminal", "capture", "events", "calls"],
        "error_fragments": ("missing_required_observer_capability", "participant_token_usage"),
    },
    MISSING_PROFILES_INVALID_CONTROL_SHA256: {
        "adapter_sha256": MISSING_PROFILES_INVALID_ADAPTER_SHA256,
        "harness_sha256": TRUSTED_HARNESS_SHA256,
        "observer_sha256": TRUSTED_OBSERVER_SHA256,
        "completed_actions": ["status", "terminal", "capture", "events"],
        "error_fragments": ("evidence action calls failed", "plugins/cortex/profiles.json"),
    },
    INCOMPLETE_AUDIT_INVALID_CONTROL_SHA256: {
        "adapter_sha256": "32976761d8f54f2d9da1209f8ef70740910b55116b49c70bb831e6c77444a0c8",
        "harness_sha256": "e6276c4832feb342e34dda404b6ccde959731703e8e2b3791a3563f5413b155b",
        "observer_sha256": "fb2a006e5f3821eee7b71b651e01ae0afc41af106a24a04ada187dfc761ce0fc",
        "completed_actions": ["status", "terminal", "capture", "events", "calls", "usage"],
        "error_fragments": ("evidence action audit failed",),
        "partial_artifacts": {
            "collection-failure.json": "ad0782c9570fde3c42e1b9c0623d20d7a2b42b6869e76fd777e461df43772e15",
            "status.txt": "0f3081086a01bce78190f2e82b42cb83174ce6325564ad4d4dd024518ddb5b4b",
            "terminal.json": "59d2583a9fd249011af886fd5d9467d1fa34e6cf144fecec9ea684c5321f7e76",
            "capture.txt": "538eea36b2365991c5ae4afe0f8e8980c4ffb9f411ab0f43bdc0ae5515ea6f80",
            "events.jsonl": "c8c5e781e1863d8e98b611a59357baaeb9e421f59c0d04f37a32cc9c02533c05",
            "calls.jsonl": "23fc0603954452931fefcf640dcdd2e7269513ce0d271211c4ea1d871619e0da",
            "usage.json": "92a242b30670625e8a08ee2d3ba85b0c81e9d68b555a88b3fa6930d6a1bcfd9f",
        },
        "invalidators": ["truncated_worker_output", "unverified_worker_assignment",
                         "forbidden_access_delivery_unverified"],
    },
    "507700c06a21aec4cfdcd128b197546a1783121b76419632ccd2b8e03d15266d": {
        "adapter_sha256": "2517a80988d8b9e016140605badac4a701a0a3337b8f7e9065fa16677746058e",
        "harness_sha256": "9c625cf6d3aa895ef2895ebf969b8cf9cf1a27f5f70f10ee761b561d618e58b6",
        "observer_sha256": "2d0aff126189a013f41c7ff0ed2ae10e29386034b214e0e9afe9cc208efe75fb",
        "completed_actions": ["status", "terminal", "capture", "events", "calls", "usage"],
        "error_fragments": ("evidence action audit failed",),
        "partial_artifacts": {
            "collection-failure.json": "3a012f7997c9919e5a31d12b8bd4a7ea9caca987a3497811abf496524c88ef7b",
            "status.txt": "0f3081086a01bce78190f2e82b42cb83174ce6325564ad4d4dd024518ddb5b4b",
            "terminal.json": "4c140a2ca785af73c06688111a74a96015f77f22cb3f6f08a40ce5bd80229e3b",
            "capture.txt": "5a15e49cc023c5ef63c98476ec0df78fa737de5ac68b63a7021b912f5bf54970",
            "events.jsonl": "4fa2b674c07cf6c944efb488645cd4a4c6c90162d50340d21ba87acd95eecde6",
            "calls.jsonl": "f24b1f6f6fa3a49ea20f044af428154c5166c28f9f9bfcbf597de24165a5f680",
            "usage.json": "a23fb5dd2b4c474cc49ab15e50a762a57d1807d9b53551c5aa8207a8c949bde1",
            "audit-cancelled.json": "de3a53316566c5ddea3999e664d75d578440f1c1e4344aa232d914255168e917",
            "status-cancelled.txt": "0f3081086a01bce78190f2e82b42cb83174ce6325564ad4d4dd024518ddb5b4b",
            "capture-cancelled.txt": "5a15e49cc023c5ef63c98476ec0df78fa737de5ac68b63a7021b912f5bf54970",
            "events-cancelled.jsonl": "4fa2b674c07cf6c944efb488645cd4a4c6c90162d50340d21ba87acd95eecde6",
            "calls-cancelled.jsonl": "f24b1f6f6fa3a49ea20f044af428154c5166c28f9f9bfcbf597de24165a5f680",
            "usage-cancelled.json": "a23fb5dd2b4c474cc49ab15e50a762a57d1807d9b53551c5aa8207a8c949bde1",
        },
        "cancelled_by_user": True,
        "cancellation_report": {"report_id": "r_4df181bc6cce",
                                "sha256": "118bbec1876a38f9982b89a1c7da31af5f2473e95e18bd6dd1271611c5dae01c"},
        "invalidator_identities": [
            ["worker_assignment_policy_unverified", "70a24ec4c660", "ec64d8432c8b"],
            ["command_wrapper_missing_receipt", "5664fefb64b8", "df1fbc8195e5"],
            ["worker_assignment_policy_unverified", "f3e51b715ed7", "00dec9a66fa5"],
            ["worker_assignment_policy_unverified", "6804696cfec3", "5b53700517ee"],
        ],
    },
}
PREAUTH_STARTUP_CLEANUP_INCIDENTS = {
    "2815ca94abaa268b7beaae066638413e362f1ca1f0b072ae8634de15e203005a": {
        "adapter_sha256": "07f88953afe5dcc157a68d0c2f54f13fab9f68bf5022958b02879f924a9a1b4f",
        "harness_sha256": "ce9be1a3b739c827800bc9db6dccc59380f41042e1b6b48b955022e81772187c",
        "observer_sha256": "2d0aff126189a013f41c7ff0ed2ae10e29386034b214e0e9afe9cc208efe75fb",
        "request_sha256": "6e143ee749061d93ea6f163edbb64b56a9b66642e3323af7e8391bae21d69cdd",
        "trust_receipt_sha256": "b35e1b37bd6b447fc8b0c26860334118d63a62f31ce188e6d60ed1a8c8db3a47",
    },
}
EVIDENCE_ACTIONS = (
    ("status", (), "status.txt"),
    ("terminal", (), "terminal.json"),
    ("capture", ("--lines", "500"), "capture.txt"),
    ("events", ("--lines", "500"), "events.jsonl"),
    ("calls", ("--limit", "10000"), "calls.jsonl"),
    ("usage", (), "usage.json"),
    ("audit", (), "audit.json"),
)
TERMINAL_RECHECKS = (
    ("status-final", "status", (), "status-final.txt"),
    ("terminal-final", "terminal", (), "terminal-final.json"),
    ("capture-final", "capture", ("--lines", "500"), "capture-final.txt"),
    ("events-final", "events", ("--lines", "500"), "events-final.jsonl"),
    ("calls-final", "calls", ("--limit", "10000"), "calls-final.jsonl"),
    ("audit-final", "audit", (), "audit-final.json"),
)
EVIDENCE_CONTRACT = {
    "begin_command": "begin-cell",
    "cell_authorization_schema": CELL_AUTH_SCHEMA,
    "command": "collect-evidence",
    "schema_version": EVIDENCE_SCHEMA,
    "artifacts": ([filename for _, _, filename in EVIDENCE_ACTIONS]
                  + [filename for _, _, _, filename in TERMINAL_RECHECKS]
                  + ["evidence-bundle.json"]),
    "bounds": {"capture_lines": 500, "events_lines": 500, "calls_limit": 10000},
    "directory_identity": ["canonical_path", "device", "inode", "owner_uid", "mode"],
    "session_lifecycle": {
        "order": ["start", "begin-cell", "send"],
        "start_completion": "ready-for-begin-after-trust-or-composer",
        "accept_trust_command": "idempotent-receipt-verification-only",
        "binding_schema": SESSION_BINDING_SCHEMA,
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
        "schema_version": ORPHAN_RECOVERY_SCHEMA,
        "eligible_authorization_states": ["issued", "submitting"],
        "captured_actions": ["status", "terminal", "capture", "calls", "events", "audit",
                             "status-final", "terminal-final", "capture-final"],
        "inactive_requirements": ["coordinator", "worker", "task", "exec", "wait"],
        "authorization": "explicit-one-time-nonce",
        "receipt": "orphan-recovery.json",
    },
    "pre_submit_abort": {
        "command": "abort-pre-submit",
        "schema_version": PRE_SUBMIT_ABORT_SCHEMA,
        "requires": ["exact-control-session", "no-request-receipt", "no-submission-receipt",
                     "no-task-or-worker", "stable-owned-trust-prompt"],
        "cleanup": "one-ownership-revalidated-stop-exact-interrupt",
    },
    "invalid_cell_cleanup": {
        "command": "cleanup-invalid-cell",
        "schema_version": INVALID_CELL_CLEANUP_SCHEMA,
        "requires": ["exact-known-control", "submitted-receipt", "incomplete-evidence-receipt",
                     "stable-terminal-task-tree", "no-active-task-tool-worker-exec-or-wait"],
        "outcome": "invalidated-no-score-and-one-ownership-revalidated-stop-exact",
    },
    "absent_runtime_gc": {
        "command": "gc-absent-runtime",
        "schema_version": ABSENT_RUNTIME_GC_SCHEMA,
        "requires": ["tmux-server-session-pane-absent", "sealed-processes-gone",
                     "no-active-task-tool-workload", "explicit-empty-audit-open-state",
                     "single-saved-session", "canonical-state-root-allowlist",
                     "canonical-empty-or-absent-events-directory",
                     "exact-known-provenance",
                     "per-mutation-full-revalidation"],
        "pre_binding_failed_launch": {
            "schema_version": PREBINDING_FAILED_LAUNCH_GC_SCHEMA,
            "requires": ["matched-failed-control-workdir-transactions",
                         "matched-unusable-stopped-failure-receipts",
                         "starting-session-without-binding-authorization-submission-or-task",
                         "single-launcher-provenance-event", "empty-capture",
                         "canonical-private-paths"],
        },
        "mutation": "atomic-archive-exact-entries-with-durable-receipt-and-full-proof",
    },
}


class AdapterError(RuntimeError):
    """A concise refusal at the evaluation compatibility boundary."""


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_file(path: Path, label: str) -> Path:
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise AdapterError(f"{label} must be a canonical regular file")
    resolved = path.resolve(strict=True)
    if resolved != path:
        raise AdapterError(f"{label} must be canonical")
    return path


def _canonical_directory(path: Path, label: str) -> Path:
    if not path.is_absolute() or path.is_symlink() or not path.is_dir():
        raise AdapterError(f"{label} must be a canonical directory")
    resolved = path.resolve(strict=True)
    if resolved != path:
        raise AdapterError(f"{label} must be canonical")
    return path


def _payload_digest(plugin: Path, canonical_version: str) -> str:
    if not plugin.is_dir() or plugin.is_symlink():
        raise AdapterError("selected arm payload is unavailable")
    digest = hashlib.sha256()
    for path in sorted(plugin.rglob("*")):
        if path.is_symlink():
            raise AdapterError("selected arm payload contains a symlink")
        if not path.is_file():
            continue
        if path.suffix in {".pyc", ".pyo"} or "__pycache__" in path.parts:
            raise AdapterError("selected arm payload contains bytecode")
        relative = path.relative_to(plugin).as_posix()
        body = path.read_bytes()
        if relative == ".codex-plugin/plugin.json":
            try:
                value = json.loads(body)
                if not isinstance(value, dict):
                    raise AdapterError("selected arm plugin identity is invalid")
                value["version"] = canonical_version
                body = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
            except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
                raise AdapterError("selected arm plugin identity is invalid") from exc
        digest.update(relative.encode() + b"\0" + str(len(body)).encode() + b"\0" + body)
    return digest.hexdigest()


def _validate_payload_identity(record: dict[str, Any], arm_root: Path, selected: dict[str, Any], arm_name: str) -> None:
    """Recheck exact version and full payload hash before every arm command."""
    expected_version, expected_digest = {
        "baseline": (BASELINE_VERSION, BASELINE_PAYLOAD_SHA256),
        "candidate": (CANDIDATE_VERSION, CANDIDATE_PAYLOAD_SHA256),
    }[arm_name]
    identities = record.get("identities")
    control_identity = identities.get(arm_name) if isinstance(identities, dict) else None
    arm_identity = selected.get("identity")
    if not isinstance(control_identity, dict) or not isinstance(arm_identity, dict):
        raise AdapterError("selected arm payload identity is malformed")
    if control_identity.get("version") != expected_version or control_identity.get("payload_sha256") != expected_digest:
        raise AdapterError("control payload identity does not match locked identity")
    if arm_identity.get("version") != expected_version or arm_identity.get("payload_sha256") != expected_digest:
        raise AdapterError("selected arm payload identity does not match locked identity")
    plugin = arm_root / "plugins" / "cortex"
    manifest = plugin / ".codex-plugin" / "plugin.json"
    if manifest.is_symlink() or not manifest.is_file():
        raise AdapterError("selected arm plugin identity is invalid")
    try:
        value = json.loads(manifest.read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AdapterError("selected arm plugin identity is invalid") from exc
    if not isinstance(value, dict) or value.get("version") != expected_version:
        raise AdapterError("selected arm archived payload version mismatch")
    if _payload_digest(plugin, expected_version.split("+", 1)[0]) != expected_digest:
        raise AdapterError("selected arm archived payload digest mismatch")


def _verify_observer_dependencies(record_root: Path, launcher: dict[str, Any]) -> None:
    """Verify the complete sealed file closure used by the common observer."""
    root_value = launcher.get("observer_bundle_root")
    manifest = launcher.get("observer_dependencies")
    if (root_value != str(record_root) or launcher.get("observer_dependencies_sha256")
            != TRUSTED_OBSERVER_DEPENDENCIES_SHA256 or not isinstance(manifest, dict)):
        raise AdapterError("common observer dependency binding is malformed")
    if not manifest or not all(isinstance(key, str) and isinstance(value, str)
                               and re.fullmatch(r"[0-9a-f]{64}", value)
                               for key, value in manifest.items()):
        raise AdapterError("common observer dependency manifest is malformed")
    required = "plugins/cortex/profiles.json"
    if required not in manifest or not any(key.startswith("plugins/cortex/skills/") for key in manifest):
        raise AdapterError("common observer dependency closure is incomplete")
    expected_keys = set(manifest)
    actual_keys = set()
    dependency_root = record_root / "plugins/cortex"
    if dependency_root.is_symlink() or not dependency_root.is_dir():
        raise AdapterError("common observer dependency root is unavailable")
    for path in dependency_root.rglob("*"):
        if path.is_symlink():
            raise AdapterError("common observer dependency contains a symlink")
        if path.is_file():
            actual_keys.add(path.relative_to(record_root).as_posix())
    if actual_keys != expected_keys:
        raise AdapterError("common observer dependency closure does not match its manifest")
    for relative, digest in manifest.items():
        path = Path(relative)
        if (path.is_absolute() or ".." in path.parts
                or not (relative == required or relative.startswith("plugins/cortex/skills/"))):
            raise AdapterError("common observer dependency path is invalid")
        target = _canonical_file(record_root / path, "common observer dependency")
        info = target.stat()
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600 or _sha256_file(target) != digest:
            raise AdapterError("common observer dependency identity mismatch")
    aggregate = hashlib.sha256(json.dumps(
        manifest, sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()
    if aggregate != TRUSTED_OBSERVER_DEPENDENCIES_SHA256:
        raise AdapterError("common observer dependency aggregate mismatch")


def _load_binding(record_path: Path, arm_name: str, *, orphan_recovery: bool = False,
                  invalid_cleanup: bool = False,
                  preauth_cleanup: bool = False) -> tuple[dict[str, Any], Path, Path, Path, dict[str, Any]]:
    record_path = _canonical_file(record_path, "control record")
    record_info = record_path.stat()
    if record_info.st_uid != os.getuid() or record_info.st_mode & 0o777 != 0o400:
        raise AdapterError("control record must be owner-read-only")
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AdapterError("control record is unreadable") from exc
    if not isinstance(record, dict) or record.get("schema_version") != SCHEMA:
        raise AdapterError("control record schema mismatch")
    neutral = record.get("neutral")
    launcher = neutral.get("live_launcher") if isinstance(neutral, dict) else None
    if not isinstance(launcher, dict) or launcher.get("mechanism") != MECHANISM:
        raise AdapterError("common live-launcher contract is missing")
    if launcher.get("model") != MODEL or launcher.get("effort") != EFFORT:
        raise AdapterError("live-launcher model/effort contract mismatch")
    harness_value = launcher.get("harness_path")
    control_digest = _sha256_file(record_path)
    cleanup_incident = (INVALID_CELL_CLEANUP_INCIDENTS.get(control_digest) if invalid_cleanup else
                        PREAUTH_STARTUP_CLEANUP_INCIDENTS.get(control_digest) if preauth_cleanup else None)
    legacy_cleanup = cleanup_incident is not None
    observer_value = launcher.get("observer_path")
    adapter_value = launcher.get("adapter_path")
    harness_digest = launcher.get("harness_sha256")
    observer_digest = launcher.get("observer_sha256")
    adapter_digest = launcher.get("adapter_sha256")
    if (not isinstance(harness_value, str) or (not legacy_cleanup and not isinstance(observer_value, str))
            or not isinstance(adapter_value, str) or not isinstance(harness_digest, str)):
        raise AdapterError("live-launcher identity is malformed")
    if not isinstance(adapter_digest, str) or not re.fullmatch(r"[0-9a-f]{64}", adapter_digest):
        raise AdapterError("adapter identity is malformed")
    harness = _canonical_file(Path(harness_value), "common live harness")
    observer = (_canonical_file(Path(__file__).with_name("cortex-desktop-dev"), "common live observer")
                if legacy_cleanup else _canonical_file(Path(observer_value), "common live observer"))
    expected_harness = record_path.parent / "harness" / "cortex-live-smoke"
    expected_observer = record_path.parent / "harness" / "cortex-desktop-dev"
    expected_adapter = Path(__file__).resolve()
    if (harness != expected_harness or (not legacy_cleanup and observer != expected_observer)
            or Path(adapter_value) != expected_adapter):
        raise AdapterError("common live-launcher path binding mismatch")
    current_binding = legacy_cleanup and (
        arm_name == "baseline"
        and harness_digest == cleanup_incident["harness_sha256"]
        and adapter_digest == cleanup_incident["adapter_sha256"]
        and _sha256_file(harness) == cleanup_incident["harness_sha256"]
        and (cleanup_incident.get("observer_sha256") is None
             or observer_digest == cleanup_incident["observer_sha256"])
        and _sha256_file(observer) == TRUSTED_OBSERVER_SHA256
    ) or (
        launcher.get("evidence_collection") == EVIDENCE_CONTRACT
        and harness_digest == TRUSTED_HARNESS_SHA256
        and observer_digest == TRUSTED_OBSERVER_SHA256
        and _sha256_file(harness) == TRUSTED_HARNESS_SHA256
        and _sha256_file(observer) == TRUSTED_OBSERVER_SHA256
        and _sha256_file(expected_adapter) == adapter_digest
    )
    if not current_binding:
        raise AdapterError("common live-launcher digest mismatch or evidence contract mismatch")
    if not legacy_cleanup:
        _verify_observer_dependencies(record_path.parent, launcher)
    arms = record.get("arms")
    selected = next((arm for arm in arms if isinstance(arm, dict) and arm.get("arm") == arm_name), None) if isinstance(arms, list) else None
    if selected is None or selected.get("live_launcher_mechanism") != MECHANISM:
        raise AdapterError("selected arm has no symmetric launcher binding")
    workdir_value = selected.get("workdir")
    if not isinstance(workdir_value, str):
        raise AdapterError("selected arm root is malformed")
    arm_root = _canonical_directory(Path(workdir_value), "selected arm root")
    expected_arm_root = record_path.parent / "arms" / arm_name
    if arm_root != expected_arm_root or selected.get("workdir_sha256") != hashlib.sha256(str(expected_arm_root).encode()).hexdigest():
        raise AdapterError("selected arm root binding mismatch")
    _validate_payload_identity(record, arm_root, selected, arm_name)
    return launcher, harness, observer, arm_root, selected


def _validate_arm_transport(arm_root: Path, selected: dict[str, Any]) -> None:
    """Verify immutable archived scripts before the shared harness imports them."""
    transport = selected.get("transport_files")
    if not isinstance(transport, dict) or set(transport) != {"launcher", "observer"}:
        raise AdapterError("selected arm transport identity is malformed")
    expected = {
        "launcher": ("scripts/cortex-dev", "0600"),
        "observer": ("scripts/cortex-desktop-dev", "0700"),
    }
    for name, (relative, expected_mode) in expected.items():
        identity = transport.get(name)
        if not isinstance(identity, dict) or set(identity) != {"relative_path", "sha256", "mode"}:
            raise AdapterError(f"selected arm {name} identity is malformed")
        if identity.get("relative_path") != relative or identity.get("mode") != expected_mode:
            raise AdapterError(f"selected arm {name} identity mismatch")
        digest = identity.get("sha256")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise AdapterError(f"selected arm {name} identity is malformed")
        path = arm_root / relative
        try:
            info = path.lstat()
        except OSError as exc:
            raise AdapterError(f"selected arm {name} is unavailable") from exc
        if path.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise AdapterError(f"selected arm {name} must be an owned regular file")
        if stat.S_IMODE(info.st_mode) != int(expected_mode, 8) or _sha256_file(path) != digest:
            raise AdapterError(f"selected arm {name} mode or digest mismatch")


def _load_harness(harness: Path, observer: Path, arm_root: Path, selected: dict[str, Any]) -> dict[str, Any]:
    _validate_arm_transport(arm_root, selected)
    namespace = runpy.run_path(str(harness), run_name="phase2_common_live_harness")
    required = ("main", "prepare_evaluation_fresh_store", "launch_command")
    if any(not callable(namespace.get(name)) for name in required):
        raise AdapterError("common harness lacks required fresh-store capabilities")
    # ``runpy`` may return a dictionary distinct from the function globals.
    # Bind the selected archive through the actual shared execution namespace.
    harness_globals = namespace["main"].__globals__
    harness_globals["ROOT"] = arm_root
    harness_globals["COMMON_OBSERVER_PATH"] = observer
    namespace["ROOT"] = arm_root
    namespace["COMMON_OBSERVER_PATH"] = observer
    if namespace.get("LIVE_MODEL") != MODEL or namespace.get("LIVE_COORDINATOR_EFFORT") != EFFORT:
        raise AdapterError("common harness model/effort capability mismatch")
    for relative in ("scripts/cortex-dev", "scripts/cortex-live-capture-sink", "scripts/cortex-desktop-dev"):
        path = arm_root / relative
        if path.is_symlink() or not path.is_file():
            raise AdapterError(f"selected arm lacks required transport support: {relative}")
    command = namespace["launch_command"](Path("/private/events"), False, MODEL, EFFORT, False, False, False)
    launcher_path = str(arm_root / "scripts/cortex-dev")
    if command.count("/bin/bash") != 1:
        raise AdapterError("common harness did not select the explicit Bash interpreter")
    interpreter_index = command.index("/bin/bash")
    if command[interpreter_index + 1:interpreter_index + 2] != [launcher_path] or command.count(launcher_path) != 1:
        raise AdapterError("common harness did not bind the selected arm launcher safely")
    return namespace


def _require_frozen_start(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--workdir", required=True)
    parser.add_argument("--evaluation-fresh-store", action="store_true")
    parser.add_argument("--model")
    parser.add_argument("--effort")
    parser.add_argument("--resume-last", action="store_true")
    try:
        parsed = parser.parse_args(argv)
    except SystemExit as exc:
        raise AdapterError("Phase 2 start arguments do not match the frozen command") from exc
    if not parsed.evaluation_fresh_store or parsed.resume_last:
        raise AdapterError("Phase 2 start requires non-resume --evaluation-fresh-store")
    if parsed.model != MODEL or parsed.effort != EFFORT:
        raise AdapterError("Phase 2 start requires gpt-5.6-luna with high effort")
    workdir = _canonical_directory(Path(parsed.workdir), "evaluation workdir")
    store = workdir / ".codex" / "cortex" / "cortex.sqlite3"
    try:
        store.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise AdapterError("evaluation store cannot be inspected") from exc
    raise AdapterError("evaluation store must be absent before launch")


def _atomic_write(path: Path, body: str) -> None:
    """Commit one owner-private evidence artifact without exposing a partial file."""
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        temporary_path.unlink(missing_ok=True)
        raise


def _read_owned_json(path: Path, label: str) -> tuple[dict[str, Any], str]:
    """Read one canonical owner-private JSON file and retain its exact digest."""
    try:
        info = path.lstat()
    except OSError as exc:
        raise AdapterError(f"{label} is unavailable") from exc
    if (path.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600):
        raise AdapterError(f"{label} must be an owner-private regular file")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AdapterError(f"{label} is invalid") from exc
    if not isinstance(value, dict):
        raise AdapterError(f"{label} is invalid")
    return value, _sha256_file(path)


def _process_start_ticks_if_present(pid: int) -> int | None:
    path = Path("/proc") / str(pid) / "stat"
    try:
        body = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise AdapterError("sealed process identity cannot be inspected") from exc
    close = body.rfind(")")
    fields = body[close + 2:].split() if close >= 0 else []
    if len(fields) <= 19 or not fields[19].isdigit():
        raise AdapterError("sealed process identity is malformed")
    return int(fields[19])


def _require_absent_tmux() -> dict[str, Any]:
    """Require the whole default tmux server to be absent, not merely one name."""
    try:
        result = subprocess.run(
            ["tmux", "list-sessions", "-F", "#{session_id}|#{session_name}|#{session_created}"],
            capture_output=True, text=True,
        )
    except OSError as exc:
        raise AdapterError("absent-runtime GC cannot inspect tmux") from exc
    if result.returncode == 0:
        raise AdapterError("absent-runtime GC refuses an existing tmux server or session")
    detail = (result.stderr or result.stdout).strip()
    if result.returncode != 1 or "no server running" not in detail:
        raise AdapterError("absent-runtime GC cannot prove the tmux server absent")
    return {"status": "absent", "exit_code": result.returncode,
            "diagnostic_sha256": hashlib.sha256(detail.encode()).hexdigest()}


def _validate_inactive_saved_evidence(workdir: Path) -> dict[str, Any]:
    """Require retained calls/audit to contain no active task, tool, wait, or session."""
    try:
        cell = workdir.parent.name
        run_root = workdir.parents[2]
    except IndexError as exc:
        raise AdapterError("stale workdir does not have the sealed cell layout") from exc
    if not re.fullmatch(r"cell-[0-9]{3}", cell) or workdir.parents[1].name != "cells":
        raise AdapterError("stale workdir does not have the sealed cell layout")
    diagnostic = run_root / "diagnostic" / cell
    calls_path, audit_path = diagnostic / "calls.jsonl", diagnostic / "audit.json"
    if any(path.is_symlink() or not path.is_file() for path in (calls_path, audit_path)):
        raise AdapterError("absent-runtime GC requires retained calls and audit evidence")
    calls_digest = _sha256_file(calls_path)
    try:
        calls = [json.loads(line) for line in calls_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AdapterError("absent-runtime GC retained activity evidence is invalid") from exc
    if not isinstance(audit, dict) or not all(isinstance(row, dict) for row in calls):
        raise AdapterError("absent-runtime GC retained activity evidence is invalid")
    if (not isinstance(audit.get("open_sessions"), list)
            or not isinstance(audit.get("open_cells"), list)
            or audit["open_sessions"] != [] or audit["open_cells"] != []):
        raise AdapterError("absent-runtime GC refuses submitted active state")
    for row in calls:
        if (row.get("outcome") not in TERMINAL_CALL_OUTCOMES
                or row.get("host_status") not in TERMINAL_HOST_STATUSES):
            raise AdapterError("absent-runtime GC refuses submitted active state")
    return {"cell_id": cell, "calls": len(calls), "calls_sha256": calls_digest,
            "audit_sha256": _sha256_file(audit_path), "open_sessions": [], "open_cells": []}


def _validate_gc_events_directory(path: Path, state_root: Path) -> dict[str, Any]:
    """Require event state absent or an exact, private, completely empty directory."""
    if path != state_root / "events":
        raise AdapterError("absent-runtime GC event directory path mismatch")
    try:
        info = path.lstat()
    except FileNotFoundError:
        return {"canonical_path": str(path), "status": "absent"}
    except OSError as exc:
        raise AdapterError("absent-runtime GC event directory is unavailable") from exc
    if (path.is_symlink() or not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o700 or path.resolve(strict=True) != path):
        raise AdapterError("absent-runtime GC event directory is not canonical owner-private state")
    if any(path.iterdir()):
        raise AdapterError("absent-runtime GC event directory must be completely empty")
    return {"canonical_path": str(path), "status": "empty", "device": info.st_dev,
            "inode": info.st_ino, "owner_uid": info.st_uid, "mode": "0700"}


def _owned_regular_file(path: Path, label: str) -> os.stat_result:
    """Return stable metadata for one canonical owner-private regular file."""
    path = _canonical_file(path, label)
    info = path.stat()
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
        raise AdapterError(f"{label} must be owner-private")
    return info


def _prebinding_entry_digest(path: Path) -> tuple[str, str]:
    """Seal a pre-binding root entry without following replacement links."""
    info = path.lstat()
    if stat.S_ISREG(info.st_mode) and not path.is_symlink():
        _owned_regular_file(path, f"pre-binding failed-launch entry {path.name}")
        return "file", _sha256_file(path)
    if path.name == "events" and stat.S_ISDIR(info.st_mode) and not path.is_symlink():
        if (path.resolve(strict=True) != path or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o700):
            raise AdapterError("pre-binding failed-launch events directory is not canonical owner-private state")
        children = list(path.iterdir())
        if len(children) != 1:
            raise AdapterError("pre-binding failed-launch requires one exact launcher event")
        child = children[0]
        _owned_regular_file(child, "pre-binding failed-launch launcher event")
        if child.parent != path or not re.fullmatch(r"launcher-[0-9a-f]{32}\.jsonl", child.name):
            raise AdapterError("pre-binding failed-launch event path is not canonical")
        digest = hashlib.sha256((child.name + "\0" + _sha256_file(child)).encode()).hexdigest()
        return "directory", digest
    raise AdapterError("pre-binding failed-launch entry has an unsafe kind")


def _validated_gc_history(state_root: Path, ignored: set[str] | None = None) -> set[str]:
    """Allow only complete byte-identical consumed GC receipt/archive pairs."""
    allowed: set[str] = set()
    ignored = ignored or set()
    patterns = (
        ("phase2-absent-runtime-gc-", "phase2-absent-runtime-archive-", ABSENT_RUNTIME_GC_SCHEMA),
        ("phase2-prebinding-failed-launch-gc-", "phase2-prebinding-failed-launch-archive-",
        PREBINDING_FAILED_LAUNCH_GC_SCHEMA),
    )
    for receipt_prefix, archive_prefix, schema in patterns:
        for receipt_path in state_root.glob(receipt_prefix + "*.json"):
            if receipt_path.name in ignored:
                continue
            receipt, _digest = _read_owned_json(receipt_path, "consumed absent-runtime GC receipt")
            plan = receipt.get("plan_sha256")
            archive_name = archive_prefix + str(plan)
            if (receipt.get("schema_version") != schema or receipt.get("status") != "consumed"
                    or not isinstance(plan, str) or not re.fullmatch(r"[0-9a-f]{64}", plan)
                    or receipt_path.name != receipt_prefix + plan + ".json"
                    or receipt.get("archive") != archive_name):
                raise AdapterError("absent-runtime GC historical receipt is invalid")
            archive = _canonical_directory(state_root / archive_name, "consumed absent-runtime GC archive")
            if archive.parent != state_root or archive.stat().st_uid != os.getuid() or archive.stat().st_mode & 0o077:
                raise AdapterError("absent-runtime GC historical archive is unsafe")
            targets, archived = receipt.get("targets"), receipt.get("archived")
            if not isinstance(targets, dict) or archived != sorted(targets):
                raise AdapterError("absent-runtime GC historical receipt is incomplete")
            entries = {path.name: path for path in archive.iterdir()}
            if set(entries) != set(targets):
                raise AdapterError("absent-runtime GC historical archive is incomplete")
            for name, expected in targets.items():
                if schema == ABSENT_RUNTIME_GC_SCHEMA:
                    if not isinstance(expected, str) or _prebinding_entry_digest(entries[name]) != ("file", expected):
                        raise AdapterError("absent-runtime GC historical target changed")
                else:
                    actual_kind, actual_digest = _prebinding_entry_digest(entries[name])
                    if expected != {"kind": actual_kind, "sha256": actual_digest}:
                        raise AdapterError("absent-runtime GC historical target changed")
            allowed.update({receipt_path.name, archive.name})
    return allowed


def _prebinding_failed_launch_inventory(
    state_root: Path, *, archive: Path | None = None,
    expected_targets: dict[str, dict[str, str]] | None = None,
    receipt_path: Path | None = None,
) -> dict[str, Any]:
    """Validate the one safe pre-binding post-commit launch-failure generation."""
    state_root = _canonical_directory(state_root, "absent-runtime state root")
    root_info = state_root.stat()
    if root_info.st_uid != os.getuid() or root_info.st_mode & 0o077:
        raise AdapterError("absent-runtime state root must be owner-private")
    ignored_history = set()
    if archive is not None:
        ignored_history.add(archive.name)
    if receipt_path is not None:
        ignored_history.add(receipt_path.name)
    history = _validated_gc_history(state_root, ignored_history)
    if archive is not None:
        archive = _canonical_directory(archive, "pre-binding failed-launch GC archive")
        if archive.parent != state_root or archive.stat().st_uid != os.getuid() or archive.stat().st_mode & 0o077:
            raise AdapterError("pre-binding failed-launch GC archive is unsafe")
    allowed_special = set(history)
    if archive is not None:
        allowed_special.add(archive.name)
    if receipt_path is not None:
        allowed_special.add(receipt_path.name)
    target_patterns = (
        r"session\.json", r"capture\.txt", r"events",
        r"phase2-control-transaction-[0-9a-f]{64}\.json",
        r"phase2-workdir-transaction-[0-9a-f]{64}\.json",
        r"phase2-control-failure-[0-9a-f]{64}\.json",
        r"phase2-workdir-failure-[0-9a-f]{64}\.json",
    )
    root_entries = list(state_root.iterdir())
    root_targets = [path for path in root_entries
                    if any(re.fullmatch(pattern, path.name) for pattern in target_patterns)]
    unknown = [path.name for path in root_entries
               if path not in root_targets and path.name not in allowed_special]
    if unknown:
        raise AdapterError("pre-binding failed-launch GC refuses unrecognized state-root entry")
    archived_targets = [] if archive is None else list(archive.iterdir())
    by_name: dict[str, list[Path]] = {}
    for path in [*root_targets, *archived_targets]:
        by_name.setdefault(path.name, []).append(path)
    expected_names = {"session.json", "capture.txt", "events"}
    for prefix in ("phase2-control-transaction-", "phase2-workdir-transaction-",
                   "phase2-control-failure-", "phase2-workdir-failure-"):
        matches = [name for name in by_name if name.startswith(prefix)]
        if len(matches) != 1:
            raise AdapterError("pre-binding failed-launch GC requires one complete failed generation")
        expected_names.add(matches[0])
    if set(by_name) != expected_names or any(len(paths) != 1 for paths in by_name.values()):
        raise AdapterError("pre-binding failed-launch GC refuses missing, duplicated, or mixed state")
    targets: dict[str, dict[str, str]] = {}
    for name, paths in by_name.items():
        kind, digest = _prebinding_entry_digest(paths[0])
        targets[name] = {"kind": kind, "sha256": digest}
    if expected_targets is not None and targets != expected_targets:
        raise AdapterError("pre-binding failed-launch GC target changed during recovery")

    json_rows: dict[str, tuple[dict[str, Any], str]] = {}
    for name, paths in by_name.items():
        if targets[name]["kind"] == "file" and name.endswith(".json"):
            value, digest = _read_owned_json(paths[0], f"pre-binding failed-launch {name}")
            json_rows[name] = (value, digest)
    tx_names = sorted(name for name in json_rows if "-transaction-" in name)
    failure_names = sorted(name for name in json_rows if "-failure-" in name)
    transactions = [json_rows[name][0] for name in tx_names]
    failures = [json_rows[name][0] for name in failure_names]
    tx_keys = {"schema_version", "status", "transaction_id", "control_record_sha256",
               "canonical_path_digest", "timestamp", "updated_at"}
    failure_keys = {"schema_version", "status", "outcome", "run_id", "control_record_sha256",
                    "canonical_path_digest", "failure_stage", "error_type", "session_cleanup", "timestamp"}
    if (any(set(row) != tx_keys for row in transactions)
            or any(set(row) != failure_keys for row in failures)
            or transactions[0] != transactions[1] or failures[0] != failures[1]):
        raise AdapterError("pre-binding failed-launch GC refuses malformed or mixed provenance")
    transaction, failure = transactions[0], failures[0]
    control, workdir_digest, transaction_id = (
        transaction.get("control_record_sha256"), transaction.get("canonical_path_digest"),
        transaction.get("transaction_id"),
    )
    numeric = lambda value: isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    if (transaction.get("schema_version") != LAUNCH_TRANSACTION_SCHEMA
            or transaction.get("status") != "failed"
            or not isinstance(control, str) or not re.fullmatch(r"[0-9a-f]{64}", control)
            or not isinstance(workdir_digest, str) or not re.fullmatch(r"[0-9a-f]{64}", workdir_digest)
            or not isinstance(transaction_id, str) or not re.fullmatch(r"[0-9a-f]{32}", transaction_id)
            or not numeric(transaction.get("timestamp")) or not numeric(transaction.get("updated_at"))
            or failure.get("schema_version") != POST_COMMIT_FAILURE_SCHEMA
            or failure.get("status") != "unusable"
            or failure.get("outcome") != "post_commit_launch_failure"
            or failure.get("session_cleanup") != "stopped"
            or failure.get("run_id") != transaction_id
            or failure.get("control_record_sha256") != control
            or failure.get("canonical_path_digest") != workdir_digest
            or not isinstance(failure.get("failure_stage"), str) or not failure["failure_stage"]
            or not isinstance(failure.get("error_type"), str) or not failure["error_type"]
            or not numeric(failure.get("timestamp"))):
        raise AdapterError("pre-binding failed-launch GC refuses unknown or nonterminal failure state")
    if (tx_names != [f"phase2-control-transaction-{control}.json",
                     f"phase2-workdir-transaction-{workdir_digest}.json"]
            or failure_names != [f"phase2-control-failure-{control}.json",
                                 f"phase2-workdir-failure-{workdir_digest}.json"]):
        raise AdapterError("pre-binding failed-launch GC refuses noncanonical provenance filenames")

    session = json_rows["session.json"][0]
    session_keys = {"workdir", "started_at", "thread_created_since", "trial_started_at", "resumed",
                    "events", "original_request_sha256", "first_submission_at", "store",
                    "codebase_memory", "model", "effort", "lifecycle_status"}
    if (set(session) != session_keys or session.get("lifecycle_status") != "starting"
            or session.get("resumed") is not False or session.get("original_request_sha256") is not None
            or session.get("first_submission_at") is not None
            or session.get("codebase_memory") is not False
            or session.get("model") != MODEL or session.get("effort") != EFFORT
            or any(key.startswith("tmux_") or "receipt" in key for key in session)
            or not all(numeric(session.get(key)) for key in
                       ("started_at", "thread_created_since", "trial_started_at"))):
        raise AdapterError("pre-binding failed-launch GC refuses bound, submitted, active, or malformed session")
    workdir = _canonical_directory(Path(session.get("workdir", "")), "pre-binding failed-launch workdir")
    cortex_dir = _canonical_directory(workdir / ".codex/cortex", "pre-binding failed-launch project state")
    if cortex_dir.stat().st_uid != os.getuid() or cortex_dir.stat().st_mode & 0o077:
        raise AdapterError("pre-binding failed-launch project state must be owner-private")
    store = workdir / ".codex/cortex/cortex.sqlite3"
    expected_digest = hashlib.sha256(f"{workdir}\0{store}".encode()).hexdigest()
    if (session.get("store") != str(store) or session.get("events") != str(state_root / "events")
            or expected_digest != workdir_digest):
        raise AdapterError("pre-binding failed-launch GC refuses path provenance mismatch")
    try:
        store.lstat()
    except FileNotFoundError:
        pass
    else:
        raise AdapterError("pre-binding failed-launch GC refuses an initialized task store")
    marker_tx, marker_failure = (
        _read_owned_json(cortex_dir / "phase2-launch-transaction.json", "project launch transaction"),
        _read_owned_json(cortex_dir / "phase2-launch-failure.json", "project launch failure"),
    )
    if marker_tx[0] != transaction or marker_failure[0] != failure:
        raise AdapterError("pre-binding failed-launch GC refuses project marker mismatch")
    capture = by_name["capture.txt"][0]
    if capture.stat().st_size != 0:
        raise AdapterError("pre-binding failed-launch GC refuses captured runtime output")
    events_dir = by_name["events"][0]
    event_path = next(events_dir.iterdir())
    if event_path.name != f"launcher-{transaction_id}.jsonl":
        raise AdapterError("pre-binding failed-launch GC refuses foreign event provenance")
    try:
        event_lines = event_path.read_text(encoding="utf-8").splitlines()
        event = json.loads(event_lines[0]) if len(event_lines) == 1 else None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AdapterError("pre-binding failed-launch GC launcher provenance is invalid") from exc
    event_keys = {"event_kind", "operation", "outcome", "run_id", "host_class",
                  "project_relative_store", "project_relative_path", "canonical_path_digest",
                  "path_digest", "existed_before", "freshness_check", "path_type", "path_check",
                  "symlink_check", "workdir_check", "privacy_check", "parent_creation", "creation",
                  "timestamp", "created_at", "launcher_digest", "observer_digest", "candidate_digest"}
    if (not isinstance(event, dict) or set(event) != event_keys
            or event.get("event_kind") != "launcher"
            or event.get("operation") != "evaluation_fresh_store" or event.get("outcome") != "success"
            or event.get("run_id") != transaction_id
            or event.get("canonical_path_digest") != workdir_digest
            or event.get("path_digest") != workdir_digest
            or event.get("host_class") != "cli"
            or event.get("project_relative_store") != ".codex/cortex/cortex.sqlite3"
            or event.get("project_relative_path") != ".codex/cortex/cortex.sqlite3"
            or event.get("existed_before") is not False
            or event.get("freshness_check") != "absent-before-launch"
            or event.get("path_type") != "absent" or event.get("path_check") != "canonical-under-workdir"
            or event.get("symlink_check") != "pass" or event.get("workdir_check") != "canonical"
            or event.get("privacy_check") != "owner-private"
            or event.get("parent_creation") not in {"atomic-private-hierarchy", "already-present-private-parents"}
            or event.get("creation") != "parent-directories-only"
            or event.get("timestamp") != transaction.get("timestamp")
            or event.get("created_at") != transaction.get("timestamp")
            or any(not isinstance(event.get(key), str) or not re.fullmatch(r"[0-9a-f]{64}", event[key])
                   for key in ("launcher_digest", "observer_digest", "candidate_digest"))):
        raise AdapterError("pre-binding failed-launch GC refuses non-launcher event state")
    tmux = _require_absent_tmux()
    generation = {"kind": "pre_binding_failed_launch", "control_record_sha256": control,
                  "canonical_path_digest": workdir_digest, "transaction_id": transaction_id,
                  "workdir": str(workdir), "store": str(store), "task_store": "absent",
                  "binding": "absent", "authorization": "absent", "submission": "absent",
                  "runtime_events": "absent", "launcher_event_sha256": _sha256_file(event_path),
                  "sealed_processes": "not_created"}
    return {"state_root": state_root, "targets": targets, "generation": generation, "tmux": tmux}


def _revalidate_prebinding_failed_launch_plan(state_root: Path, archive: Path,
                                               receipt: dict[str, Any]) -> dict[str, Any]:
    """Repeat every semantic, runtime, path, kind, and byte proof."""
    inventory = _prebinding_failed_launch_inventory(
        state_root, archive=archive, expected_targets=receipt.get("targets"),
        receipt_path=state_root / f"phase2-prebinding-failed-launch-gc-{receipt.get('plan_sha256')}.json",
    )
    if inventory["generation"] != receipt.get("generation"):
        raise AdapterError("pre-binding failed-launch GC proof changed during recovery")
    archived = sorted(path.name for path in archive.iterdir())
    recorded, ordered = receipt.get("archived"), sorted(receipt["targets"])
    if (not isinstance(recorded, list) or recorded != ordered[:len(recorded)]
            or archived != ordered[:len(archived)] or len(archived) - len(recorded) not in {0, 1}):
        raise AdapterError("pre-binding failed-launch GC receipt/archive progress mismatch")
    if receipt.get("status") == "consumed" and archived != ordered:
        raise AdapterError("pre-binding failed-launch GC consumed receipt is incomplete")
    inventory["archived"] = archived
    return inventory


def _gc_prebinding_failed_launch(state_root: Path, authorization_nonce: str) -> dict[str, Any]:
    """Atomically archive only one fully proven failed pre-binding launch."""
    if not re.fullmatch(r"[0-9a-f]{64}", authorization_nonce):
        raise AdapterError("absent-runtime GC requires an explicit one-time authorization nonce")
    state_root = _canonical_directory(state_root, "absent-runtime state root")
    nonce_sha256 = hashlib.sha256(authorization_nonce.encode()).hexdigest()
    prior = []
    for path in state_root.glob("phase2-prebinding-failed-launch-gc-*.json"):
        receipt, _digest = _read_owned_json(path, "pre-binding failed-launch GC receipt")
        if receipt.get("authorization_nonce_sha256") == nonce_sha256:
            prior.append((path, receipt))
    if len(prior) > 1:
        raise AdapterError("pre-binding failed-launch GC has conflicting prior receipts")
    if prior:
        receipt_path, receipt = prior[0]
        if (receipt.get("schema_version") != PREBINDING_FAILED_LAUNCH_GC_SCHEMA
                or receipt.get("status") not in {"prepared", "consumed"}
                or not isinstance(receipt.get("targets"), dict)):
            raise AdapterError("pre-binding failed-launch GC prior receipt is invalid")
        plan_digest = receipt.get("plan_sha256")
        archive = state_root / f"phase2-prebinding-failed-launch-archive-{plan_digest}"
        expected_plan = hashlib.sha256(json.dumps({
            "authorization_nonce": authorization_nonce, "targets": receipt["targets"],
            "generation": receipt.get("generation"),
        }, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        if (not isinstance(plan_digest, str)
                or receipt_path.name != f"phase2-prebinding-failed-launch-gc-{plan_digest}.json"
                or not secrets.compare_digest(plan_digest, expected_plan)):
            raise AdapterError("pre-binding failed-launch GC prior receipt changed")
        resumed = _revalidate_prebinding_failed_launch_plan(state_root, archive, receipt)
        if receipt["archived"] != resumed["archived"]:
            receipt["archived"] = resumed["archived"]
            _atomic_write(receipt_path, json.dumps(receipt, indent=2, sort_keys=True) + "\n")
        if receipt["status"] == "consumed":
            return {**receipt, "replayed": True}
    else:
        first = _prebinding_failed_launch_inventory(state_root)
        plan_digest = hashlib.sha256(json.dumps({
            "authorization_nonce": authorization_nonce, "targets": first["targets"],
            "generation": first["generation"],
        }, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        receipt_path = state_root / f"phase2-prebinding-failed-launch-gc-{plan_digest}.json"
        archive = state_root / f"phase2-prebinding-failed-launch-archive-{plan_digest}"
        second = _prebinding_failed_launch_inventory(state_root)
        if second["targets"] != first["targets"] or second["generation"] != first["generation"]:
            raise AdapterError("pre-binding failed-launch GC state changed before mutation")
        archive.mkdir(mode=0o700)
        receipt = {"schema_version": PREBINDING_FAILED_LAUNCH_GC_SCHEMA, "status": "prepared",
                   "plan_sha256": plan_digest, "authorization_nonce_sha256": nonce_sha256,
                   "created_at": time.time(), "tmux": second["tmux"],
                   "generation": second["generation"], "targets": second["targets"], "archived": []}
        _atomic_write(receipt_path, json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    if archive.is_symlink() or archive.stat().st_uid != os.getuid() or archive.stat().st_mode & 0o077:
        raise AdapterError("pre-binding failed-launch GC archive is unsafe")
    for name in sorted(receipt["targets"]):
        _revalidate_prebinding_failed_launch_plan(state_root, archive, receipt)
        source, destination = state_root / name, archive / name
        if destination.exists():
            if source.exists() or _prebinding_entry_digest(destination) != (
                    receipt["targets"][name]["kind"], receipt["targets"][name]["sha256"]):
                raise AdapterError("pre-binding failed-launch GC partial archive is inconsistent")
        else:
            if _prebinding_entry_digest(source) != (
                    receipt["targets"][name]["kind"], receipt["targets"][name]["sha256"]):
                raise AdapterError("pre-binding failed-launch GC target changed during mutation")
            os.replace(source, destination)
        receipt["archived"] = sorted(set(receipt["archived"]) | {name})
        _atomic_write(receipt_path, json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    _revalidate_prebinding_failed_launch_plan(state_root, archive, receipt)
    receipt["status"] = "consumed"
    receipt["consumed_at"] = time.time()
    receipt["archive"] = archive.name
    _atomic_write(receipt_path, json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    return receipt


def _absent_runtime_inventory(state_root: Path, *, archive: Path | None = None,
                              expected_targets: dict[str, str] | None = None,
                              receipt_path: Path | None = None) -> dict[str, Any]:
    """Validate and enumerate only known Phase 2 state generations."""
    state_root = _canonical_directory(state_root, "absent-runtime state root")
    info = state_root.stat()
    if info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise AdapterError("absent-runtime state root must be owner-private")
    runtime_name_patterns = (
        r"session\.json", r"last\.json", r"phase2-cell-authorization\.json",
        r"phase2-cell-authorization-[0-9a-f]{64}-[0-9a-f]{64}\.json",
        r"phase2-session-binding-[0-9a-f]{64}(?:-[0-9a-f]{64})?\.json",
        r"phase2-pre-submit-abort-[0-9a-f]{64}-[0-9a-f]{64}\.json",
        r"phase2-control-transaction-[0-9a-f]{64}\.json",
        r"phase2-workdir-transaction-[0-9a-f]{64}\.json",
    )
    recognized_name = lambda name: any(re.fullmatch(pattern, name) for pattern in runtime_name_patterns)
    root_entries = list(state_root.iterdir())
    root_paths = [path for path in root_entries if recognized_name(path.name)]
    archive_paths: list[Path] = []
    if archive is not None:
        archive = _canonical_directory(archive, "absent-runtime GC archive")
        if archive.parent != state_root or archive.stat().st_uid != os.getuid() or archive.stat().st_mode & 0o077:
            raise AdapterError("absent-runtime GC archive is unsafe")
        archive_paths = [path for path in archive.iterdir() if path.is_file() or path.is_symlink()]
    ignored_history = set()
    if archive is not None:
        ignored_history.add(archive.name)
    if receipt_path is not None:
        ignored_history.add(receipt_path.name)
    allowed_nonruntime = {"events", *_validated_gc_history(state_root, ignored_history)}
    if archive is not None:
        allowed_nonruntime.add(archive.name)
    if receipt_path is not None:
        allowed_nonruntime.add(receipt_path.name)
    unknown = [path.name for path in root_entries
               if path not in root_paths and path.name not in allowed_nonruntime]
    if unknown:
        raise AdapterError("absent-runtime GC refuses unrecognized state-root entry")
    by_name: dict[str, list[Path]] = {}
    for path in [*root_paths, *archive_paths]:
        by_name.setdefault(path.name, []).append(path)
    if expected_targets is not None:
        if set(by_name) != set(expected_targets) or any(len(value) != 1 for value in by_name.values()):
            raise AdapterError("absent-runtime GC refuses new, missing, duplicated, or foreign state")
        for name, paths_for_name in by_name.items():
            if _sha256_file(paths_for_name[0]) != expected_targets.get(name):
                raise AdapterError("absent-runtime GC target changed during recovery")
    paths = sorted((values[0] for values in by_name.values()), key=lambda path: path.name)
    if not paths:
        raise AdapterError("absent-runtime GC found no stale state")
    records: dict[str, tuple[Path, dict[str, Any], str]] = {}
    for path in paths:
        value, digest = _read_owned_json(path, f"stale state {path.name}")
        records[path.name] = (path, value, digest)

    transactions: dict[str, dict[str, Any]] = {}
    for name, (_path, value, _digest) in records.items():
        if name.startswith(("phase2-control-transaction-", "phase2-workdir-transaction-")):
            control = value.get("control_record_sha256")
            if (value.get("schema_version") != "phase2-cli-launch-transaction-v1"
                    or value.get("status") not in {"launched", "failed"}
                    or not isinstance(control, str) or not re.fullmatch(r"[0-9a-f]{64}", control)):
                raise AdapterError("absent-runtime GC refuses unknown transaction state")
            expected_name = (f"phase2-control-transaction-{control}.json"
                             if name.startswith("phase2-control-transaction-")
                             else f"phase2-workdir-transaction-{value.get('canonical_path_digest')}.json")
            if name != expected_name:
                raise AdapterError("absent-runtime GC refuses noncanonical transaction filename")
            kind = "control" if name.startswith("phase2-control-transaction-") else "workdir"
            transaction = transactions.setdefault(control, {})
            if kind in transaction:
                raise AdapterError("absent-runtime GC refuses duplicate transaction provenance")
            transaction[kind] = value
            if len(transaction) == 2 and transaction["control"].get("canonical_path_digest") != transaction["workdir"].get("canonical_path_digest"):
                raise AdapterError("absent-runtime GC refuses conflicting transaction provenance")

    bindings: dict[str, dict[str, Any]] = {}
    for name, (_path, value, _digest) in records.items():
        if name.startswith("phase2-session-binding-"):
            control, receipt = value.get("control_record_sha256"), value.get("session_receipt")
            if (value.get("schema_version") != SESSION_BINDING_SCHEMA
                    or not isinstance(control, str) or not re.fullmatch(r"[0-9a-f]{64}", control)
                    or not isinstance(receipt, str) or not re.fullmatch(r"[0-9a-f]{64}", receipt)
                    or name not in {f"phase2-session-binding-{control}.json",
                                    f"phase2-session-binding-{control}-{receipt}.json"}):
                raise AdapterError("absent-runtime GC refuses foreign or unknown session binding")
            if receipt in bindings or any(row.get("control_record_sha256") == control for row in bindings.values()):
                raise AdapterError("absent-runtime GC refuses duplicate session binding")
            bindings[receipt] = value

    generations: dict[str, dict[str, Any]] = {}
    for name, (_path, value, digest) in records.items():
        if name == "phase2-cell-authorization.json" or name.startswith("phase2-cell-authorization-"):
            control, receipt = value.get("control_record_sha256"), value.get("session_receipt")
            if (value.get("schema_version") != CELL_AUTH_SCHEMA
                    or value.get("status") not in {"submitted", "collected", "consumed", "invalidated", "issued", "submitting"}
                    or not isinstance(control, str) or not re.fullmatch(r"[0-9a-f]{64}", control)
                    or not isinstance(receipt, str) or not re.fullmatch(r"[0-9a-f]{64}", receipt)
                    or control not in transactions or set(transactions[control]) != {"control", "workdir"}):
                raise AdapterError("absent-runtime GC refuses foreign or unknown authorization")
            canonical_names = {"phase2-cell-authorization.json",
                               f"phase2-cell-authorization-{control}-{receipt}.json"}
            if name not in canonical_names:
                raise AdapterError("absent-runtime GC refuses noncanonical authorization filename")
            if value.get("status") in {"issued", "submitting"}:
                raise AdapterError("absent-runtime GC requires terminal or explicitly invalidated state")
            if value.get("status") == "submitted":
                _validate_submission(value, {
                    "original_request_sha256": value.get("submission_request_sha256"),
                    "first_submission_at": value.get("submitted_at"),
                    "native_user_turn_receipt": value.get("submission_native_user_turn"),
                })
            workdir = _canonical_directory(Path(value.get("workdir", "")), "stale authorization workdir")
            activity = _validate_inactive_saved_evidence(workdir)
            if (not isinstance(value.get("cell_id"), str)
                    or not re.fullmatch(r"cell-[0-9]{3}", value["cell_id"])
                    or activity["cell_id"] != value["cell_id"]):
                raise AdapterError("absent-runtime GC refuses authorization cell-path mismatch")
            if control in generations:
                raise AdapterError("absent-runtime GC refuses duplicate authorization provenance")
            generations[control] = {"control_record_sha256": control, "session_receipt": receipt,
                                    "authorization": name, "authorization_sha256": digest,
                                    "authorization_status": value.get("status"), "binding": None,
                                    "workdir": str(workdir), "activity": activity}

    if "last.json" in records and "session.json" in records:
        raise AdapterError("absent-runtime GC refuses simultaneous saved session states")
    last = records.get("last.json") or records.get("session.json")
    if last is None:
        raise AdapterError("absent-runtime GC requires one exact saved session state")
    if last is not None:
        _path, value, digest = last
        control, receipt = value.get("phase2_control_sha256"), value.get("session_receipt")
        binding = bindings.get(receipt)
        if (value.get("lifecycle_status") != "started" or not binding
                or binding.get("control_record_sha256") != control):
            raise AdapterError("absent-runtime GC refuses foreign or unknown saved session")
        sealed_fields = set(binding) - {"schema_version", "control_record_sha256", "session_receipt"}
        if any(value.get(field) != binding.get(field) for field in sealed_fields):
            raise AdapterError("absent-runtime GC saved session does not match sealed provenance")
        events_value = value.get("events")
        if not isinstance(events_value, str) or binding.get("events") != events_value:
            raise AdapterError("absent-runtime GC saved session event provenance is invalid")
        events_identity = _validate_gc_events_directory(Path(events_value), state_root)
        generation = generations.setdefault(control, {
            "control_record_sha256": control, "session_receipt": receipt,
            "authorization": None, "authorization_sha256": None,
            "authorization_status": "absent", "workdir": value.get("workdir"),
            "activity": _validate_inactive_saved_evidence(
                _canonical_directory(Path(value.get("workdir", "")), "stale session workdir")),
        })
        generation["binding"] = next(name for name, (_p, row, _d) in records.items()
                                     if row is binding)
        generation["binding_sha256"] = next(d for _n, (_p, row, d) in records.items()
                                                   if row is binding)
        generation["sealed_processes"] = {
            key: binding.get(key) for key in
            ("tmux_server_pid", "tmux_pane_pid", "tmux_pane_start_ticks")
        }
        generation["saved_state_sha256"] = digest
        generation["events"] = events_identity

    if (not generations or set(transactions) != set(generations)
            or any(set(pair) != {"control", "workdir"} for pair in transactions.values())):
        raise AdapterError("absent-runtime GC refuses unbound or incomplete control provenance")
    for receipt, binding in bindings.items():
        control = binding["control_record_sha256"]
        if control not in generations or generations[control]["session_receipt"] != receipt:
            raise AdapterError("absent-runtime GC refuses foreign or unknown session binding")
        for pid_field, ticks_field in (("tmux_server_pid", None), ("tmux_pane_pid", "tmux_pane_start_ticks")):
            pid = binding.get(pid_field)
            if pid is None:
                continue
            if not isinstance(pid, int) or isinstance(pid, bool) or pid < 1:
                raise AdapterError("absent-runtime GC sealed process identity is invalid")
            actual = _process_start_ticks_if_present(pid)
            expected = binding.get(ticks_field) if ticks_field else None
            if actual is not None and (expected is None or actual == expected):
                raise AdapterError("absent-runtime GC refuses a live or ambiguous sealed process")
    invalidations: set[tuple[str, str]] = set()
    for name, (_path, value, _digest) in records.items():
        if name.startswith("phase2-pre-submit-abort-"):
            control, receipt = value.get("control_record_sha256"), value.get("session_receipt")
            if (value.get("schema_version") != PRE_SUBMIT_ABORT_SCHEMA or value.get("status") != "consumed"
                    or control not in generations or generations[control]["session_receipt"] != receipt
                    or name != f"phase2-pre-submit-abort-{control}-{receipt}.json"
                    or (control, receipt) in invalidations):
                raise AdapterError("absent-runtime GC refuses foreign or invalidated-report mismatch")
            invalidations.add((control, receipt))
    return {"state_root": state_root, "records": records, "generations": generations,
            "tmux": _require_absent_tmux()}


def _revalidate_gc_plan(state_root: Path, archive: Path, receipt: dict[str, Any]) -> dict[str, Any]:
    """Repeat the complete runtime/activity/PID/file proof for one prepared plan."""
    targets = receipt.get("targets")
    generations = receipt.get("generations")
    if not isinstance(targets, dict) or not isinstance(generations, dict):
        raise AdapterError("absent-runtime GC prior receipt is invalid")
    inventory = _absent_runtime_inventory(
        state_root, archive=archive, expected_targets=targets,
        receipt_path=state_root / f"phase2-absent-runtime-gc-{receipt.get('plan_sha256')}.json",
    )
    current_hashes = {name: digest for name, (_path, _value, digest) in inventory["records"].items()}
    if current_hashes != targets or inventory["generations"] != generations:
        raise AdapterError("absent-runtime GC proof changed during recovery")
    archived = sorted(path.name for path in archive.iterdir() if path.is_file())
    recorded = receipt.get("archived")
    ordered_targets = sorted(targets)
    if (not isinstance(recorded, list) or recorded != ordered_targets[:len(recorded)]
            or archived != ordered_targets[:len(archived)] or len(archived) - len(recorded) not in {0, 1}):
        raise AdapterError("absent-runtime GC receipt/archive progress mismatch")
    if receipt.get("status") == "consumed" and archived != sorted(targets):
        raise AdapterError("absent-runtime GC consumed receipt is incomplete")
    inventory["archived"] = archived
    return inventory


def _gc_absent_runtime(state_root: Path, authorization_nonce: str) -> dict[str, Any]:
    """Archive exact stale files after two identical absent-runtime proofs."""
    if not re.fullmatch(r"[0-9a-f]{64}", authorization_nonce):
        raise AdapterError("absent-runtime GC requires an explicit one-time authorization nonce")
    state_root = _canonical_directory(state_root, "absent-runtime state root")
    nonce_sha256 = hashlib.sha256(authorization_nonce.encode()).hexdigest()
    exact_replays: list[tuple[Path, dict[str, Any], str]] = []
    for prefix, schema in (("phase2-absent-runtime-gc-", ABSENT_RUNTIME_GC_SCHEMA),
                           ("phase2-prebinding-failed-launch-gc-",
                            PREBINDING_FAILED_LAUNCH_GC_SCHEMA)):
        for path in state_root.glob(prefix + "*.json"):
            candidate, _digest = _read_owned_json(path, "absent-runtime GC receipt")
            if candidate.get("authorization_nonce_sha256") == nonce_sha256:
                exact_replays.append((path, candidate, schema))
    if len(exact_replays) > 1:
        raise AdapterError("absent-runtime GC has conflicting prior receipts")
    if exact_replays and exact_replays[0][1].get("status") == "consumed":
        receipt_path, receipt, schema = exact_replays[0]
        plan_digest = receipt.get("plan_sha256")
        prefix = ("phase2-absent-runtime-gc-" if schema == ABSENT_RUNTIME_GC_SCHEMA
                  else "phase2-prebinding-failed-launch-gc-")
        payload_key = "generations" if schema == ABSENT_RUNTIME_GC_SCHEMA else "generation"
        expected_plan = hashlib.sha256(json.dumps({
            "authorization_nonce": authorization_nonce, "targets": receipt.get("targets"),
            payload_key: receipt.get(payload_key),
        }, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        if (receipt.get("schema_version") != schema or not isinstance(plan_digest, str)
                or receipt_path.name != f"{prefix}{plan_digest}.json"
                or not secrets.compare_digest(plan_digest, expected_plan)):
            raise AdapterError("absent-runtime GC prior receipt changed")
        _validated_gc_history(state_root)
        return {**receipt, "replayed": True}
    normal_markers = [path for path in state_root.iterdir()
                      if path.name in {"last.json", "phase2-cell-authorization.json"}
                      or path.name.startswith(("phase2-cell-authorization-",
                                               "phase2-session-binding-",
                                               "phase2-pre-submit-abort-"))]
    failed_markers = list(state_root.glob("phase2-*-failure-*.json"))
    if failed_markers and normal_markers:
        raise AdapterError("absent-runtime GC refuses ambiguous simultaneous generation kinds")
    if failed_markers or (exact_replays
                          and exact_replays[0][2] == PREBINDING_FAILED_LAUNCH_GC_SCHEMA):
        return _gc_prebinding_failed_launch(state_root, authorization_nonce)
    prior = []
    for path in state_root.glob("phase2-absent-runtime-gc-*.json"):
        receipt, _digest = _read_owned_json(path, "absent-runtime GC receipt")
        if receipt.get("authorization_nonce_sha256") == nonce_sha256:
            prior.append((path, receipt))
    if len(prior) > 1:
        raise AdapterError("absent-runtime GC has conflicting prior receipts")
    if prior:
        receipt_path, receipt = prior[0]
        if receipt.get("schema_version") != ABSENT_RUNTIME_GC_SCHEMA:
            raise AdapterError("absent-runtime GC prior receipt is invalid")
        if receipt.get("status") not in {"prepared", "consumed"} or not isinstance(receipt.get("targets"), dict):
            raise AdapterError("absent-runtime GC prior receipt is invalid")
        plan_digest = receipt.get("plan_sha256")
        archive = state_root / f"phase2-absent-runtime-archive-{plan_digest}"
        if not isinstance(plan_digest, str) or receipt_path.name != f"phase2-absent-runtime-gc-{plan_digest}.json":
            raise AdapterError("absent-runtime GC prior receipt is invalid")
        expected_plan = hashlib.sha256(json.dumps({
            "authorization_nonce": authorization_nonce, "targets": receipt["targets"],
            "generations": receipt.get("generations"),
        }, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        if not secrets.compare_digest(plan_digest, expected_plan):
            raise AdapterError("absent-runtime GC prior receipt changed")
        resumed = _revalidate_gc_plan(state_root, archive, receipt)
        if receipt["archived"] != resumed["archived"]:
            receipt["archived"] = resumed["archived"]
            _atomic_write(receipt_path, json.dumps(receipt, indent=2, sort_keys=True) + "\n")
        if receipt.get("status") == "consumed":
            return {**receipt, "replayed": True}
        first = {"state_root": state_root, "records": {
            name: (state_root / name, {}, digest) for name, digest in receipt["targets"].items()
        }}
        target_hashes = dict(receipt["targets"])
    else:
        first = _absent_runtime_inventory(state_root)
        target_hashes = {name: digest for name, (_path, _value, digest) in first["records"].items()}
        plan_digest = hashlib.sha256(json.dumps({
            "authorization_nonce": authorization_nonce, "targets": target_hashes,
            "generations": first["generations"],
        }, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        receipt_path = first["state_root"] / f"phase2-absent-runtime-gc-{plan_digest}.json"
        archive = first["state_root"] / f"phase2-absent-runtime-archive-{plan_digest}"
    if not prior:
        second = _absent_runtime_inventory(first["state_root"])
        second_hashes = {name: digest for name, (_path, _value, digest) in second["records"].items()}
        if second_hashes != target_hashes or second["generations"] != first["generations"]:
            raise AdapterError("absent-runtime GC state changed before mutation")
        archive.mkdir(mode=0o700)
        receipt = {"schema_version": ABSENT_RUNTIME_GC_SCHEMA, "status": "prepared",
                   "plan_sha256": plan_digest, "authorization_nonce_sha256": nonce_sha256,
                   "created_at": time.time(), "tmux": second["tmux"], "generations": second["generations"],
                   "targets": target_hashes, "archived": []}
        _atomic_write(receipt_path, json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    if archive.is_symlink() or archive.stat().st_uid != os.getuid() or archive.stat().st_mode & 0o077:
        raise AdapterError("absent-runtime GC archive is unsafe")
    try:
        for name in sorted(target_hashes):
            _revalidate_gc_plan(first["state_root"], archive, receipt)
            source, destination = first["state_root"] / name, archive / name
            if destination.exists():
                if _sha256_file(destination) != target_hashes[name] or source.exists():
                    raise AdapterError("absent-runtime GC partial archive is inconsistent")
            else:
                if _sha256_file(source) != target_hashes[name]:
                    raise AdapterError("absent-runtime GC target changed during mutation")
                os.replace(source, destination)
            receipt["archived"] = sorted(set(receipt["archived"]) | {name})
            _atomic_write(receipt_path, json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    except BaseException:
        raise
    _revalidate_gc_plan(first["state_root"], archive, receipt)
    receipt["status"] = "consumed"
    receipt["consumed_at"] = time.time()
    receipt["archive"] = archive.name
    _atomic_write(receipt_path, json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    return receipt


def _session_context(namespace: dict[str, Any]) -> dict[str, Any]:
    state_reader = namespace.get("state")
    state_root = namespace.get("STATE")
    if not callable(state_reader) or not isinstance(state_root, Path):
        raise AdapterError("common harness lacks required session-state capabilities")
    state_root = _canonical_directory(state_root, "live session state")
    if state_root.stat().st_uid != os.getuid() or state_root.stat().st_mode & 0o077:
        raise AdapterError("live session state must be owner-private")
    try:
        state = state_reader()
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        raise AdapterError("current live session state is unavailable") from exc
    if not isinstance(state, dict):
        raise AdapterError("current live session state is malformed")
    workdir_value, store_value, events_value = state.get("workdir"), state.get("store"), state.get("events")
    server_pid, session_name = state.get("tmux_server_pid"), state.get("tmux_session_name")
    session_id, session_created = state.get("tmux_session_id"), state.get("tmux_session_created")
    pane_id, pane_pid, pane_start = state.get("tmux_pane_id"), state.get("tmux_pane_pid"), state.get("tmux_pane_start_ticks")
    started_at, thread_created_since = state.get("started_at"), state.get("thread_created_since")
    if not all(isinstance(value, str) for value in (workdir_value, store_value, events_value)):
        raise AdapterError("current live session paths are malformed")
    if not all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in (started_at, thread_created_since)):
        raise AdapterError("current live session timestamps are malformed")
    if (not isinstance(server_pid, int) or isinstance(server_pid, bool) or server_pid < 1
            or session_name != "cortex-markdown-smoke"
            or not isinstance(session_id, str) or not re.fullmatch(r"\$[0-9]+", session_id)
            or not isinstance(session_created, int) or isinstance(session_created, bool) or session_created < 1
            or not isinstance(pane_id, str) or not re.fullmatch(r"%[0-9]+", pane_id)
            or not isinstance(pane_pid, int) or isinstance(pane_pid, bool) or pane_pid < 1
            or not isinstance(pane_start, int) or isinstance(pane_start, bool) or pane_start < 1):
        raise AdapterError("current live session lacks an exact owned tmux pane receipt")
    workdir = _canonical_directory(Path(workdir_value), "live evaluation workdir")
    store = Path(store_value)
    events = _canonical_directory(Path(events_value), "live event directory")
    if store != workdir / ".codex" / "cortex" / "cortex.sqlite3" or events.parent != state_root:
        raise AdapterError("current live session path binding mismatch")
    fields = {
        "workdir": str(workdir),
        "store": str(store),
        "events": str(events),
        "started_at": started_at,
        "thread_created_since": thread_created_since,
        "tmux_server_pid": server_pid,
        "tmux_session_name": session_name,
        "tmux_session_id": session_id,
        "tmux_session_created": session_created,
        "tmux_pane_id": pane_id,
        "tmux_pane_pid": pane_pid,
        "tmux_pane_start_ticks": pane_start,
    }
    control_digest = state.get("phase2_control_sha256")
    receipt = state.get("session_receipt")
    if state.get("lifecycle_status") != "started":
        raise AdapterError("current live session has not completed its start transition")
    if not isinstance(control_digest, str) or not re.fullmatch(r"[0-9a-f]{64}", control_digest):
        raise AdapterError("current live session lacks its scoped control binding")
    expected_receipt = hashlib.sha256(
        json.dumps(fields, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if not isinstance(receipt, str) or not secrets.compare_digest(receipt, expected_receipt):
        raise AdapterError("current live session receipt changed after start")
    binding_path = state_root / f"phase2-session-binding-{control_digest}-{receipt}.json"
    if (binding_path.is_symlink() or not binding_path.is_file()
            or binding_path.stat().st_uid != os.getuid()
            or stat.S_IMODE(binding_path.stat().st_mode) != 0o600):
        raise AdapterError("current live session binding receipt is unavailable")
    try:
        binding = json.loads(binding_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AdapterError("current live session binding receipt is invalid") from exc
    expected_binding = {
        "schema_version": SESSION_BINDING_SCHEMA,
        "control_record_sha256": control_digest,
        **fields,
        "session_receipt": receipt,
    }
    if binding != expected_binding:
        raise AdapterError("current live session binding changed after start")
    return {
        **fields,
        "control_record_sha256": control_digest,
        "session_receipt": receipt,
        "state_root": state_root,
    }


def _authorization_path(context: dict[str, Any]) -> Path:
    return context["state_root"] / (
        "phase2-cell-authorization-"
        f"{context['control_record_sha256']}-{context['session_receipt']}.json"
    )


def _read_authorization(context: dict[str, Any]) -> dict[str, Any]:
    path = _authorization_path(context)
    if path.is_symlink() or not path.is_file() or stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise AdapterError("current cell authorization is unavailable")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AdapterError("current cell authorization is invalid") from exc
    if not isinstance(value, dict) or value.get("schema_version") != CELL_AUTH_SCHEMA:
        raise AdapterError("current cell authorization is invalid")
    return value


def _authorization_identity(authorization: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "cell_id", "nonce", "control_record_sha256", "arm", "workdir", "store",
        "started_at", "thread_created_since", "session_receipt", "submission_request_sha256",
        "submitted_at", "submission_native_user_turn", "submission_receipt", "thread_id", "evidence_directory",
    )
    return {field: authorization.get(field) for field in fields}


def _submission_receipt(nonce: str, request_digest: str, submitted_at: int | float,
                        native_user_turn: dict[str, Any]) -> str:
    native_digest = hashlib.sha256(
        json.dumps(native_user_turn, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return hashlib.sha256(
        f"{nonce}\0{request_digest}\0{submitted_at}\0{native_digest}".encode()
    ).hexdigest()


def _validate_submission(authorization: dict[str, Any], state: dict[str, Any]) -> None:
    request_digest = authorization.get("submission_request_sha256")
    submitted_at = authorization.get("submitted_at")
    receipt = authorization.get("submission_receipt")
    native_user_turn = authorization.get("submission_native_user_turn")
    if not isinstance(request_digest, str) or not re.fullmatch(r"[0-9a-f]{64}", request_digest):
        raise AdapterError("submitted cell lacks an exact request receipt")
    if (
        not isinstance(submitted_at, (int, float))
        or isinstance(submitted_at, bool)
        or not math.isfinite(submitted_at)
        or submitted_at < authorization["started_at"]
    ):
        raise AdapterError("submitted cell lacks a valid submission timestamp")
    if (
        not isinstance(native_user_turn, dict)
        or not isinstance(native_user_turn.get("thread_id"), str)
        or not native_user_turn["thread_id"]
        or not isinstance(native_user_turn.get("user_turn_sha256"), str)
        or not re.fullmatch(r"[0-9a-f]{64}", native_user_turn["user_turn_sha256"])
        or native_user_turn.get("user_turn_source_format") not in {
            "response-item-message-v1", "event-user-message-v1",
        }
        or native_user_turn.get("control_record_sha256") != authorization["control_record_sha256"]
        or native_user_turn.get("session_receipt") != authorization["session_receipt"]
        or not isinstance(native_user_turn.get("prompt_sha256"), str)
        or not re.fullmatch(r"[0-9a-f]{64}", native_user_turn["prompt_sha256"])
        or not isinstance(native_user_turn.get("owned_codex_pid"), int)
        or isinstance(native_user_turn.get("owned_codex_pid"), bool)
        or native_user_turn["owned_codex_pid"] < 1
        or not isinstance(native_user_turn.get("owned_codex_start_ticks"), int)
        or isinstance(native_user_turn.get("owned_codex_start_ticks"), bool)
        or native_user_turn["owned_codex_start_ticks"] < 1
        or not isinstance(native_user_turn.get("owned_rollout_descriptors"), list)
        or not native_user_turn["owned_rollout_descriptors"]
        or any(
            not isinstance(row, dict)
            or set(row) != {"fd", "device", "inode"}
            or any(not isinstance(row.get(key), int) or isinstance(row.get(key), bool)
                   or row[key] < 0 for key in ("fd", "device", "inode"))
            for row in native_user_turn.get("owned_rollout_descriptors", [])
        )
    ):
        raise AdapterError("submitted cell lacks an exact native user-turn source")
    expected_receipt = _submission_receipt(
        authorization["nonce"], request_digest, submitted_at, native_user_turn,
    )
    if not isinstance(receipt, str) or not secrets.compare_digest(receipt, expected_receipt):
        raise AdapterError("submitted cell has an inconsistent submission receipt")
    if (state.get("original_request_sha256") != request_digest
            or state.get("first_submission_at") != submitted_at
            or state.get("native_user_turn_receipt") != native_user_turn):
        raise AdapterError("submitted cell does not match the exact transport receipt")


def _require_authorization(namespace: dict[str, Any], control_record: Path, arm: str, status: str) -> tuple[dict[str, Any], dict[str, Any]]:
    context = _session_context(namespace)
    authorization = _read_authorization(context)
    if authorization.get("status") != status:
        raise AdapterError(f"current cell authorization must be {status}")
    expected = {
        "control_record_sha256": _sha256_file(control_record),
        "arm": arm,
        "workdir": context["workdir"],
        "store": context["store"],
        "started_at": context["started_at"],
        "thread_created_since": context["thread_created_since"],
        "session_receipt": context["session_receipt"],
    }
    if any(authorization.get(key) != value for key, value in expected.items()):
        raise AdapterError("cell authorization does not match the current live session")
    if context["control_record_sha256"] != expected["control_record_sha256"]:
        raise AdapterError("current live session belongs to a different control record")
    if not isinstance(authorization.get("cell_id"), str) or not re.fullmatch(r"cell-[0-9]{3}", authorization["cell_id"]):
        raise AdapterError("cell authorization has invalid cell identity")
    if not isinstance(authorization.get("nonce"), str) or not re.fullmatch(r"[0-9a-f]{64}", authorization["nonce"]):
        raise AdapterError("cell authorization has invalid nonce")
    if authorization.get("status") in {"submitted", "collected", "consumed"}:
        try:
            state = namespace["state"]()
        except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
            raise AdapterError("current live session state is unavailable") from exc
        if not isinstance(state, dict):
            raise AdapterError("current live session state is malformed")
        _validate_submission(authorization, state)
    elif any(authorization.get(field) is not None for field in (
        "submission_request_sha256", "submitted_at", "submission_native_user_turn", "submission_receipt",
    )):
        raise AdapterError("unsubmitted cell has unexpected submission evidence")
    return authorization, context


def _begin_cell(namespace: dict[str, Any], control_record: Path, arm: str, cell_id: str) -> dict[str, Any]:
    if not re.fullmatch(r"cell-[0-9]{3}", cell_id):
        raise AdapterError("cell id must use cell-NNN format")
    context = _session_context(namespace)
    if context["control_record_sha256"] != _sha256_file(control_record):
        raise AdapterError("current live session belongs to a different control record")
    path = _authorization_path(context)
    if path.exists():
        previous = _read_authorization(context)
        if previous.get("status") != "consumed":
            raise AdapterError("current live session already has an unconsumed cell authorization")
    authorization = {
        "schema_version": CELL_AUTH_SCHEMA,
        "status": "issued",
        "cell_id": cell_id,
        "nonce": secrets.token_hex(32),
        "control_record_sha256": _sha256_file(control_record),
        "arm": arm,
        "workdir": context["workdir"],
        "store": context["store"],
        "started_at": context["started_at"],
        "thread_created_since": context["thread_created_since"],
        "session_receipt": context["session_receipt"],
        "submission_request_sha256": None,
        "submitted_at": None,
        "submission_native_user_turn": None,
        "submission_receipt": None,
        "thread_id": None,
    }
    _atomic_write(path, json.dumps(authorization, indent=2, sort_keys=True) + "\n")
    return authorization


def _mark_submitting(namespace: dict[str, Any], control_record: Path, arm: str) -> None:
    authorization, context = _require_authorization(namespace, control_record, arm, "issued")
    authorization["status"] = "submitting"
    _atomic_write(_authorization_path(context), json.dumps(authorization, indent=2, sort_keys=True) + "\n")


def _require_trust_ready(namespace: dict[str, Any]) -> dict[str, Any]:
    capability = namespace.get("_require_trust_receipt")
    if not callable(capability):
        raise AdapterError("common harness lacks the sealed trust/composer capability")
    try:
        return capability(namespace["state"]())
    except (OSError, RuntimeError, ValueError) as exc:
        raise AdapterError(str(exc)) from exc


def _complete_phase2_startup(namespace: dict[str, Any], harness: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """Own the one bounded trust/composer transition after the exact launch."""
    before = _session_context(namespace)
    _invoke_harness(namespace, harness, "startup-ready", ())
    after = _session_context(namespace)
    if before != after:
        raise AdapterError("owned session identity changed during startup trust transition")
    return after, _require_trust_ready(namespace)


def _mark_submitted(namespace: dict[str, Any], control_record: Path, arm: str) -> None:
    authorization, context = _require_authorization(namespace, control_record, arm, "submitting")
    state = namespace["state"]()
    request_digest, submitted_at = state.get("original_request_sha256"), state.get("first_submission_at")
    native_user_turn = state.get("native_user_turn_receipt")
    if not isinstance(request_digest, str) or not re.fullmatch(r"[0-9a-f]{64}", request_digest):
        raise AdapterError("submitted cell lacks an exact request receipt")
    if (
        not isinstance(submitted_at, (int, float))
        or isinstance(submitted_at, bool)
        or not math.isfinite(submitted_at)
        or submitted_at < authorization["started_at"]
    ):
        raise AdapterError("submitted cell lacks a valid submission timestamp")
    authorization["status"] = "submitted"
    authorization["submission_request_sha256"] = request_digest
    authorization["submitted_at"] = submitted_at
    authorization["submission_native_user_turn"] = native_user_turn
    authorization["submission_receipt"] = _submission_receipt(
        authorization["nonce"], request_digest, submitted_at, native_user_turn,
    )
    _validate_submission(authorization, state)
    _atomic_write(_authorization_path(context), json.dumps(authorization, indent=2, sort_keys=True) + "\n")


def _prepare_evidence_directory(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute() or path.resolve() != path or path.is_symlink():
        raise AdapterError("evidence directory must be an absolute canonical path")
    parent = path.parent
    if parent.is_symlink() or not parent.is_dir() or parent.resolve() != parent:
        raise AdapterError("evidence directory parent must be a canonical directory")
    if path.exists():
        raise AdapterError("evidence directory must be absent")
    path.mkdir(mode=0o700)
    if path.stat().st_uid != os.getuid() or stat.S_IMODE(path.stat().st_mode) != 0o700:
        raise AdapterError("evidence directory must be owner-private")
    return path


def _evidence_directory_identity(path: Path) -> dict[str, Any]:
    directory = _canonical_directory(path, "evidence directory")
    info = directory.stat()
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise AdapterError("evidence directory must be owner-private")
    return {
        "canonical_path": str(directory),
        "device": info.st_dev,
        "inode": info.st_ino,
        "owner_uid": info.st_uid,
        "mode": "0700",
    }


def _parse_json_lines(body: str, action: str) -> list[dict[str, Any]]:
    rows = []
    for line in body.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise AdapterError(f"{action} evidence is not valid JSON lines") from exc
        if not isinstance(row, dict):
            raise AdapterError(f"{action} evidence row is not an object")
        rows.append(row)
    if not rows:
        raise AdapterError(f"{action} evidence is empty")
    return rows


def _validate_evidence(action: str, body: str) -> dict[str, Any]:
    if not body.strip():
        raise AdapterError(f"{action} evidence is empty")
    if action == "status":
        match = re.fullmatch(r"\S+ dead=([01]) status=([^\r\n]*)", body.strip())
        if match is None or (match.group(2) and not match.group(2).isdigit()):
            raise AdapterError("status evidence does not identify exactly one pane")
        return {"dead": match.group(1) == "1", "exit_status": int(match.group(2)) if match.group(2) else None}
    if action == "terminal":
        try:
            value = json.loads(body)
        except json.JSONDecodeError as exc:
            raise AdapterError("terminal evidence is not a JSON object") from exc
        required = {"schema_version", "session_id", "pane_id", "pane_pid", "current_command", "dead", "dead_status", "descendants"}
        if (not isinstance(value, dict) or set(value) != required
                or value.get("schema_version") != "cortex-live-terminal-snapshot-v1"
                or not isinstance(value.get("descendants"), list)):
            raise AdapterError("terminal evidence schema mismatch")
        return {"descendants": len(value["descendants"])}
    if action == "capture":
        return {"lines": len(body.splitlines())}
    if action in {"events", "calls"}:
        rows = _parse_json_lines(body, action)
        if action == "events" and any(not isinstance(row.get("outcome"), str) for row in rows):
            raise AdapterError("events evidence lacks outcome metadata")
        metadata: dict[str, Any] = {"rows": len(rows)}
        if action == "calls":
            roots = {
                row.get("thread_id") for row in rows
                if row.get("role") == "coordinator" and row.get("parent_thread_id") is None
                and isinstance(row.get("thread_id"), str) and row["thread_id"]
            }
            if len(roots) != 1:
                raise AdapterError("calls evidence lacks one coordinator thread receipt")
            metadata["thread_id"] = roots.pop()
        return metadata
    try:
        value = json.loads(body)
    except json.JSONDecodeError as exc:
        raise AdapterError(f"{action} evidence is not a JSON object") from exc
    if not isinstance(value, dict):
        raise AdapterError(f"{action} evidence is not a JSON object")
    if action == "usage":
        required = {"status", "wall_seconds", "totals", "participants"}
        if not required <= set(value) or not isinstance(value["status"], str) or not isinstance(value["participants"], list):
            raise AdapterError("usage evidence schema mismatch")
        return {"participants": len(value["participants"]), "status": value["status"]}
    required = {
        "mcp_events", "hook_actions", "host_calls", "failures", "hook_failures",
        "host_failures", "orchestration_error_history", "orchestration_host_failures",
        "orchestration_policy_violations", "open_sessions", "open_cells",
        "schema_version", "evidence_valid", "score_eligible",
        "evidence_integrity_invalidators", "quality_findings",
    }
    if not required <= set(value):
        raise AdapterError("audit evidence schema mismatch")
    if not all(isinstance(value[key], int) and not isinstance(value[key], bool) for key in ("mcp_events", "hook_actions", "host_calls")):
        raise AdapterError("audit evidence count schema mismatch")
    if value["mcp_events"] < 1 or value["host_calls"] < 1:
        raise AdapterError("audit evidence is missing required observed activity")
    if (value["schema_version"] != "cortex-evidence-quality-classification-v1"
            or not isinstance(value["evidence_valid"], bool)
            or not isinstance(value["score_eligible"], bool)
            or value["score_eligible"] != value["evidence_valid"]
            or not isinstance(value["evidence_integrity_invalidators"], list)
            or not isinstance(value["quality_findings"], list)):
        raise AdapterError("audit classification schema mismatch")
    if not value["evidence_valid"] or value["evidence_integrity_invalidators"]:
        raise AdapterError("audit evidence is integrity-invalid and not score eligible")
    return {"hook_actions": value["hook_actions"], "host_calls": value["host_calls"],
            "mcp_events": value["mcp_events"], "quality_findings": len(value["quality_findings"])}


def _validate_inactive_activity(calls_body: str, audit_body: str, *,
                                require_completed_task: bool = False,
                                allow_terminal_failures: bool = False) -> tuple[str, dict[str, Any] | None]:
    calls = _parse_json_lines(calls_body, "calls")
    try:
        audit = json.loads(audit_body)
    except json.JSONDecodeError as exc:
        raise AdapterError("terminal proof audit is not a JSON object") from exc
    if not isinstance(audit, dict) or audit.get("open_sessions") not in ([], None) or audit.get("open_cells") not in ([], None):
        raise AdapterError("terminal proof refused while an exec session or cell is active")
    def terminal_call(row: dict[str, Any]) -> bool:
        if row.get("host_status") not in TERMINAL_HOST_STATUSES:return False
        if row.get("outcome") in TERMINAL_CALL_OUTCOMES:return True
        if not allow_terminal_failures or row.get("outcome") not in {"truncated", "not_dispatched"}:
            return False
        # Cleanup may preserve historical failures, but only with an explicit
        # terminal wrapper timestamp or an authoritative native execution row.
        return bool(row.get("completed_timestamp"))
    if any(not terminal_call(row) for row in calls):
        raise AdapterError("terminal proof refused while coordinator, worker, task, exec, or wait activity is active")
    for row in calls:
        if "wait" not in str(row.get("tool", "")).lower():
            continue
        if row.get("outcome") != "success" or row.get("host_status") not in TERMINAL_HOST_STATUSES:
            raise AdapterError("terminal proof requires an explicitly completed terminal wait")
        if not isinstance(row.get("completed_timestamp"), str) or not row["completed_timestamp"]:
            raise AdapterError("terminal proof requires an explicitly completed terminal wait")
    spawned = {
        row.get("spawned_thread_id") for row in calls
        if row.get("tool") == "spawn_agent" and row.get("outcome") == "success"
        and isinstance(row.get("spawned_thread_id"), str)
    }
    completed = {
        row.get("thread_id") for row in calls
        if row.get("tool") == "native_agent_result" and row.get("outcome") == "success"
    }
    if spawned - completed:
        raise AdapterError("terminal proof refused while a worker is active")
    roots = {
        row.get("thread_id") for row in calls
        if row.get("role") == "coordinator" and row.get("parent_thread_id") is None
        and isinstance(row.get("thread_id"), str) and row["thread_id"]
    }
    if len(roots) != 1:
        raise AdapterError("terminal proof requires exactly one coordinator task tree")
    root = roots.pop()
    thread_rows = [row for row in calls if isinstance(row.get("thread_id"), str)]
    thread_ids = {row["thread_id"] for row in thread_rows}
    parent_sets = {
        thread_id: {row.get("parent_thread_id") for row in thread_rows if row["thread_id"] == thread_id}
        for thread_id in thread_ids - {root}
    }
    if any(len(values) != 1 for values in parent_sets.values()):
        raise AdapterError("terminal proof contains conflicting task ownership")
    parents = {
        thread_id: next(iter(values)) for thread_id, values in parent_sets.items()
    }
    if any(parent not in thread_ids for parent in parents.values()):
        raise AdapterError("terminal proof contains a foreign or disconnected task tree")
    for thread_id in parents:
        visited = {thread_id}
        parent = parents[thread_id]
        while parent != root:
            if parent in visited or parent not in parents:
                raise AdapterError("terminal proof contains a foreign or cyclic task tree")
            visited.add(parent)
            parent = parents[parent]
    final_report = None
    if require_completed_task:
        workers = thread_ids - {root}
        terminal_workers = {
            row.get("thread_id") for row in calls
            if row.get("tool") == "native_agent_result" and row.get("outcome") == "success"
        }
        if workers - terminal_workers:
            raise AdapterError("terminal proof refused while a worker lacks a terminal result")
        if any(
            next(row for row in reversed(thread_rows) if row["thread_id"] == worker).get("tool")
            != "native_agent_result"
            for worker in workers
        ):
            raise AdapterError("terminal proof refused after post-terminal worker activity")
        final_reports = [
            row for row in calls
            if row.get("thread_id") == root and row.get("parent_thread_id") is None
            and row.get("tool") == "mcp__cortex__write_report"
            and row.get("outcome") == "success" and row.get("host_status") in (None, "completed")
            and isinstance(row.get("report_id"), str)
            and REPORT_ID_PATTERN.fullmatch(row["report_id"])
            and isinstance(row.get("draft_id"), str)
            and DRAFT_ID_PATTERN.fullmatch(row["draft_id"])
        ]
        if not final_reports or thread_rows[-1] is not final_reports[-1]:
            raise AdapterError("terminal proof requires the coordinator's final report receipt")
        final_report = final_reports[-1]
    return root, final_report


def _validate_owned_codex_tree(snapshot: dict[str, Any]) -> None:
    descendants = snapshot.get("descendants")
    if not isinstance(descendants, list) or not descendants:
        raise AdapterError("idle composer proof requires the owned Codex process tree")
    parsed: dict[int, tuple[int, str]] = {}
    for row in descendants:
        if (
            not isinstance(row, dict) or set(row) != {"pid", "ppid", "command"}
            or not isinstance(row.get("pid"), int) or isinstance(row.get("pid"), bool)
            or not isinstance(row.get("ppid"), int) or isinstance(row.get("ppid"), bool)
            or not isinstance(row.get("command"), str) or not row["command"]
            or row["pid"] in parsed
        ):
            raise AdapterError("idle composer proof has a malformed process tree")
        parsed[row["pid"]] = (row["ppid"], row["command"])
    direct = [pid for pid, (ppid, command) in parsed.items()
              if ppid == snapshot["pane_pid"] and command == "codex"]
    if len(direct) != 1 or any(ppid == snapshot["pane_pid"] and pid not in direct
                               for pid, (ppid, _) in parsed.items()):
        raise AdapterError("idle composer proof does not own exactly one Codex task process")
    for pid in parsed:
        visited = {pid}
        parent = parsed[pid][0]
        while parent != snapshot["pane_pid"]:
            if parent in visited or parent not in parsed:
                raise AdapterError("idle composer proof contains a foreign or cyclic process")
            visited.add(parent)
            parent = parsed[parent][0]


def _validate_idle_composer(capture: str, context: dict[str, Any]) -> None:
    tail = capture[-5000:]
    worked = list(re.finditer(r"─ Worked for [^\r\n]+ ─+", tail))
    footer = f"gpt-5.6-luna high · {context['workdir']} ·"
    # The owner-only pipe records terminal cursor motion rather than a rendered
    # screen, so the prompt/footer can precede the final completion divider in
    # the byte stream even though both are visibly present in the idle screen.
    if (
        not worked or "› Ask Codex to do anything" not in tail or footer not in tail
        or "esc to interrupt" in tail[worked[-1].start():]
    ):
        raise AdapterError("terminal proof requires the exact idle Codex composer state")


def _validate_terminal_proof(bodies: dict[str, str], context: dict[str, Any]) -> dict[str, Any]:
    stable = (("status", "status-final"), ("terminal", "terminal-final"),
              ("capture", "capture-final"), ("events", "events-final"),
              ("calls", "calls-final"), ("audit", "audit-final"))
    if any(bodies[first] != bodies[second] for first, second in stable):
        raise AdapterError("terminal status, activity, capture, or process snapshot changed during evidence collection")
    status = re.fullmatch(r"(\S+) dead=([01]) status=([^\r\n]*)", bodies["status"].strip())
    if status is None:
        raise AdapterError("terminal status evidence is malformed")
    snapshot = json.loads(bodies["terminal"])
    expected_identity = {
        "session_id": context["tmux_session_id"], "pane_id": context["tmux_pane_id"],
        "pane_pid": context["tmux_pane_pid"],
    }
    if any(snapshot.get(key) != value for key, value in expected_identity.items()):
        raise AdapterError("terminal process snapshot does not match the owned session receipt")
    dead = status.group(2) == "1"
    dead_status = int(status.group(3)) if status.group(3) else None
    if (snapshot.get("current_command") != status.group(1) or snapshot.get("dead") is not dead
            or snapshot.get("dead_status") != dead_status):
        raise AdapterError("terminal status and process snapshot disagree")
    command = snapshot.get("current_command")
    if command == "codex" and not dead:
        if dead_status is not None:
            raise AdapterError("idle composer proof requires an empty tmux status")
        _validate_owned_codex_tree(snapshot)
        root, final_report = _validate_inactive_activity(
            bodies["calls"], bodies["audit"], require_completed_task=True,
        )
        assert final_report is not None
        events = _parse_json_lines(bodies["events"], "events")
        root_report_events = [
            row for row in events
            if row.get("operation") == "write_report" and row.get("outcome") == "success"
            and row.get("thread_id") == root
        ]
        matching_events = [
            row for row in root_report_events
            if isinstance(row.get("report_id"), str)
            and REPORT_ID_PATTERN.fullmatch(row["report_id"])
            and isinstance(row.get("draft_id"), str)
            and DRAFT_ID_PATTERN.fullmatch(row["draft_id"])
            and row["report_id"] == final_report["report_id"]
            and row["draft_id"] == final_report["draft_id"]
        ]
        if (
            len(matching_events) != 1
            or any(row.get("report_id") != final_report["report_id"] for row in root_report_events)
        ):
            raise AdapterError("terminal proof requires the exact owned coordinator final report event")
        if re.search(r"^Cortex live-dev exit=", bodies["capture"], flags=re.MULTILINE):
            raise AdapterError("idle composer proof refuses a stale or foreign exit marker")
        _validate_idle_composer(bodies["capture"], context)
        return {"pane_state": "idle-live-codex-composer", "exit_status": None,
                "session_receipt": context["session_receipt"]}
    if command != "bash":
        raise AdapterError("terminal proof requires the owned bash pane or idle Codex composer")
    if snapshot.get("descendants") != []:
        raise AdapterError("terminal proof refused while a launcher, Codex, or child process remains")
    if dead:
        if dead_status != 0:
            raise AdapterError("terminal proof requires a successful dead pane")
        pane_state = "successful-dead-bash"
    else:
        if dead_status is not None:
            raise AdapterError("terminal proof requires an idle live bash pane with empty tmux status")
        pane_state = "idle-live-bash"
    markers = re.findall(r"^Cortex live-dev exit=([0-9]+)$", bodies["capture"], flags=re.MULTILINE)
    if markers != ["0"]:
        raise AdapterError("terminal proof requires exactly one current successful live-dev exit marker")
    _validate_inactive_activity(bodies["calls"], bodies["audit"])
    return {"pane_state": pane_state, "exit_status": 0, "session_receipt": context["session_receipt"]}


def _invoke_harness(namespace: dict[str, Any], harness: Path, action: str, arguments: tuple[str, ...]) -> str:
    stdout = io.StringIO()
    stderr = io.StringIO()
    previous = sys.argv
    try:
        sys.argv = [str(harness), action, *arguments]
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = namespace["main"]()
    except SystemExit as exc:
        result = exc.code
    except (OSError, RuntimeError, ValueError) as exc:
        detail = stderr.getvalue().strip() or stdout.getvalue().strip() or str(exc)
        raise AdapterError(f"evidence action {action} failed: {detail[:240]}") from exc
    finally:
        sys.argv = previous
    exit_code = int(result) if isinstance(result, int) else 0
    if exit_code != 0:
        detail = stderr.getvalue().strip() or stdout.getvalue().strip()
        raise AdapterError(f"evidence action {action} failed with exit {exit_code}: {detail[:240]}")
    if stderr.getvalue().strip():
        raise AdapterError(f"evidence action {action} produced unexpected stderr")
    return stdout.getvalue()


def _collect_evidence(namespace: dict[str, Any], harness: Path, directory: Path, control_record: Path, arm: str,
                      authorization: dict[str, Any], context: dict[str, Any]) -> None:
    artifacts: dict[str, Any] = {}
    bodies: dict[str, str] = {}
    directory_identity = _evidence_directory_identity(directory)
    try:
        for action, arguments, filename in EVIDENCE_ACTIONS:
            body = _invoke_harness(namespace, harness, action, arguments)
            metadata = _validate_evidence(action, body)
            bodies[action] = body
            destination = directory / filename
            _atomic_write(destination, body)
            artifacts[action] = {
                "arguments": list(arguments),
                "filename": filename,
                "sha256": _sha256_file(destination),
                **metadata,
            }
        for name, action, arguments, filename in TERMINAL_RECHECKS:
            body = _invoke_harness(namespace, harness, action, arguments)
            _validate_evidence(action, body)
            bodies[name] = body
            destination = directory / filename
            _atomic_write(destination, body)
            artifacts[name] = {
                "action": action, "arguments": list(arguments), "filename": filename,
                "sha256": _sha256_file(destination),
            }
        final_context = _session_context(namespace)
        if final_context["session_receipt"] != context["session_receipt"]:
            raise AdapterError("owned session receipt changed during evidence collection")
        terminal_proof = _validate_terminal_proof(bodies, context)
        thread_id = artifacts["calls"].get("thread_id")
        if not isinstance(thread_id, str) or not thread_id:
            raise AdapterError("collection lacks a coordinator thread receipt")
        if _evidence_directory_identity(directory) != directory_identity:
            raise AdapterError("evidence directory identity changed during collection")
        authorization["thread_id"] = thread_id
        authorization["evidence_directory"] = directory_identity
        manifest = {
            "schema_version": EVIDENCE_SCHEMA,
            "status": "complete",
            "arm": arm,
            "control_record_sha256": _sha256_file(control_record),
            "evidence_directory": directory_identity,
            "cell_identity": _authorization_identity(authorization),
            "terminal_proof": terminal_proof,
            "actions": artifacts,
        }
        manifest_path = directory / "evidence-bundle.json"
        _atomic_write(manifest_path, json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        authorization["status"] = "collected"
        authorization["evidence_bundle_sha256"] = _sha256_file(manifest_path)
        _atomic_write(_authorization_path(context), json.dumps(authorization, indent=2, sort_keys=True) + "\n")
    except (AdapterError, OSError, ValueError) as exc:
        _atomic_write(directory / "collection-failure.json", json.dumps({
            "schema_version": EVIDENCE_SCHEMA,
            "status": "incomplete",
            "completed_actions": list(artifacts),
            "error": str(exc),
        }, indent=2, sort_keys=True) + "\n")
        raise


def _validate_evidence_bundle(directory_value: str, control_record: Path, arm: str,
                              namespace: dict[str, Any]) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    authorization, context = _require_authorization(namespace, control_record, arm, "collected")
    directory = _canonical_directory(Path(directory_value), "evidence directory")
    directory_identity = _evidence_directory_identity(directory)
    manifest_path = directory / "evidence-bundle.json"
    if manifest_path.is_symlink() or not manifest_path.is_file() or stat.S_IMODE(manifest_path.stat().st_mode) != 0o600:
        raise AdapterError("stop requires a complete sealed evidence bundle")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AdapterError("stop requires a valid sealed evidence bundle") from exc
    if not isinstance(manifest, dict) or manifest.get("schema_version") != EVIDENCE_SCHEMA or manifest.get("status") != "complete":
        raise AdapterError("stop requires a complete sealed evidence bundle")
    if manifest.get("arm") != arm or manifest.get("control_record_sha256") != _sha256_file(control_record):
        raise AdapterError("evidence bundle does not match the selected control and arm")
    if manifest.get("evidence_directory") != directory_identity or authorization.get("evidence_directory") != directory_identity:
        raise AdapterError("evidence bundle does not match its authorized directory identity")
    if manifest.get("cell_identity") != _authorization_identity(authorization):
        raise AdapterError("evidence bundle does not match the current cell authorization")
    if authorization.get("evidence_bundle_sha256") != _sha256_file(manifest_path):
        raise AdapterError("evidence bundle authorization digest mismatch")
    actions = manifest.get("actions")
    expected = {action: (arguments, filename) for action, arguments, filename in EVIDENCE_ACTIONS}
    expected.update({name: (arguments, filename) for name, _, arguments, filename in TERMINAL_RECHECKS})
    if not isinstance(actions, dict) or set(actions) != set(expected):
        raise AdapterError("evidence bundle action set is incomplete")
    bodies: dict[str, str] = {}
    for name, (arguments, filename) in expected.items():
        entry = actions[name]
        artifact = directory / filename
        if not isinstance(entry, dict) or entry.get("arguments") != list(arguments) or entry.get("filename") != filename:
            raise AdapterError(f"evidence bundle {name} metadata mismatch")
        if artifact.is_symlink() or not artifact.is_file() or stat.S_IMODE(artifact.stat().st_mode) != 0o600:
            raise AdapterError(f"evidence bundle {name} artifact is unavailable")
        if entry.get("sha256") != _sha256_file(artifact):
            raise AdapterError(f"evidence bundle {name} artifact digest mismatch")
        body = artifact.read_text(encoding="utf-8")
        bodies[name] = body
        validation_action = entry.get("action", name)
        _validate_evidence(validation_action, body)
    terminal_proof = _validate_terminal_proof(bodies, context)
    if manifest.get("terminal_proof") != terminal_proof:
        raise AdapterError("evidence bundle terminal proof mismatch")
    return directory, authorization, context


def _revalidate_terminal_before_stop(namespace: dict[str, Any], harness: Path, directory: Path,
                                     context: dict[str, Any]) -> None:
    final_context = _session_context(namespace)
    if final_context["session_receipt"] != context["session_receipt"]:
        raise AdapterError("owned session receipt changed before stop")
    for name, action, arguments, filename in TERMINAL_RECHECKS:
        current = _invoke_harness(namespace, harness, action, arguments)
        expected = (directory / filename).read_text(encoding="utf-8")
        if current != expected:
            raise AdapterError("terminal status or process snapshot changed before stop")


def _pre_submit_abort_path(context: dict[str, Any]) -> Path:
    return context["state_root"] / (
        "phase2-pre-submit-abort-"
        f"{context['control_record_sha256']}-{context['session_receipt']}.json"
    )


def _exact_stop_arguments(context: dict[str, Any], *, interrupt: bool) -> tuple[str, ...]:
    values = (
        "--control-sha256", context["control_record_sha256"],
        "--session-receipt", context["session_receipt"],
        "--server-pid", str(context["tmux_server_pid"]),
        "--session-name", context["tmux_session_name"],
        "--session-id", context["tmux_session_id"],
        "--session-created", str(context["tmux_session_created"]),
        "--pane-id", context["tmux_pane_id"],
        "--pane-pid", str(context["tmux_pane_pid"]),
        "--pane-start-ticks", str(context["tmux_pane_start_ticks"]),
    )
    return (("--interrupt",) + values) if interrupt else values


def _validate_pre_submit_abort(captured: dict[str, tuple[str, int]], context: dict[str, Any],
                               authorization_status: str, prompt: str | None) -> dict[str, Any]:
    stable = (("status", "status-final"), ("terminal", "terminal-final"),
              ("capture", "capture-final"), ("screen", "screen-final"), ("calls", "calls-final"),
              ("events", "events-final"), ("audit", "audit-final"))
    if any(captured[first] != captured[second] for first, second in stable):
        raise AdapterError("pre-submit abort refused because owned session evidence changed")
    if captured["status"][1] != 0 or captured["status"][0].strip() != "codex dead=0 status=":
        raise AdapterError("pre-submit abort requires the exact live Codex pane")
    try:
        terminal = json.loads(captured["terminal"][0])
    except json.JSONDecodeError as exc:
        raise AdapterError("pre-submit abort terminal evidence is malformed") from exc
    expected = {
        "session_id": context["tmux_session_id"], "pane_id": context["tmux_pane_id"],
        "pane_pid": context["tmux_pane_pid"], "current_command": "codex",
        "dead": False, "dead_status": None,
    }
    if not isinstance(terminal, dict) or any(terminal.get(key) != value for key, value in expected.items()):
        raise AdapterError("pre-submit abort terminal evidence is foreign or inactive")
    _validate_owned_codex_tree(terminal)
    screen = captured["screen"][0]
    compact_screen = re.sub(r"\s+", "", screen).lower()
    if authorization_status in {"absent", "issued"}:
        if ("doyoutrustthecontentsofthisdirectory?" not in compact_screen
                or "pressentertocontinue" not in compact_screen):
            raise AdapterError("pre-submit abort requires the unchanged trust prompt")
        pane_state = "live-trust-prompt"
    else:
        if ("Ask Codex to do anything" not in screen
                or "doyoutrustthecontentsofthisdirectory?" in re.sub(r"\s+", "", screen[-1200:]).lower()
                or (prompt is not None and prompt.rstrip("\r\n") in screen)):
            raise AdapterError("submitting-state abort requires the unchanged empty composer")
        pane_state = "live-empty-composer-after-no-submit"
    if re.search(r"^Cortex live-dev exit=", captured["capture"][0], flags=re.MULTILINE):
        raise AdapterError("pre-submit abort refuses a terminal exit marker")
    calls = (_parse_json_lines(captured["calls"][0], "calls")
             if captured["calls"][0].strip() else [])
    allowed_tools = {"mcp__cortex__evaluation_fresh_store", "mcp__cortex__initialize"}
    if any(
        row.get("role") != "runtime" or row.get("tool") not in allowed_tools
        or row.get("outcome") != "success" or row.get("thread_id") is not None
        or row.get("parent_thread_id") is not None
        for row in calls
    ):
        raise AdapterError("pre-submit abort refused because a task, worker, or foreign call exists")
    events = (_parse_json_lines(captured["events"][0], "events")
              if captured["events"][0].strip() else [])
    if any(
        row.get("operation") not in {"evaluation_fresh_store", "initialize"}
        or row.get("outcome") != "success"
        or (row.get("operation") == "evaluation_fresh_store" and row.get("event_kind") != "launcher")
        for row in events
    ):
        raise AdapterError("pre-submit abort refused because task or foreign event evidence exists")
    try:
        audit = json.loads(captured["audit"][0])
    except json.JSONDecodeError as exc:
        raise AdapterError("pre-submit abort audit evidence is malformed") from exc
    empty_fields = ("failures", "hook_failures", "host_failures", "orchestration_error_history",
                    "orchestration_host_failures", "policy_violations", "worker_policy_violations",
                    "orchestration_policy_violations", "open_sessions", "open_cells")
    if not isinstance(audit, dict) or any(audit.get(field) not in ([], None) for field in empty_fields):
        raise AdapterError("pre-submit abort refused because activity or audit failure remains")
    return {"calls": len(calls), "events": len(events), "pane_state": pane_state}


def _abort_pre_submit(namespace: dict[str, Any], harness: Path, control_record: Path,
                      arm: str, abort_dir_value: str, prompt_file: str | None = None) -> dict[str, Any]:
    context = _session_context(namespace)
    control_digest = _sha256_file(control_record)
    if context["control_record_sha256"] != control_digest:
        raise AdapterError("pre-submit abort control or session ownership mismatch")
    state = namespace["state"]()
    if state.get("first_submission_at") is not None or state.get("native_user_turn_receipt") is not None:
        raise AdapterError("pre-submit abort refuses any accepted submission receipt")
    authorization_path = _authorization_path(context)
    authorization_status = "absent"
    if authorization_path.exists() or authorization_path.is_symlink():
        authorization = _read_authorization(context)
        expected = {
            "control_record_sha256": control_digest, "arm": arm,
            "workdir": context["workdir"], "store": context["store"],
            "started_at": context["started_at"],
            "thread_created_since": context["thread_created_since"],
            "session_receipt": context["session_receipt"],
        }
        if (authorization.get("status") not in {"issued", "submitting"}
                or any(authorization.get(key) != value for key, value in expected.items())
                or any(authorization.get(field) is not None for field in (
                    "submission_request_sha256", "submitted_at", "submission_native_user_turn", "submission_receipt",
                ))):
            raise AdapterError("pre-submit abort refuses submitted, foreign, or invalid authorization")
        authorization_status = authorization["status"]
    startup_incident = PREAUTH_STARTUP_CLEANUP_INCIDENTS.get(control_digest)
    if startup_incident is not None:
        trust_path = namespace["_phase2_trust_receipt_path"](state)
        send_path = namespace["_phase2_send_receipt_path"](state)
        if (arm != "baseline" or authorization_status != "absent"
                or state.get("original_request_sha256") != startup_incident["request_sha256"]
                or send_path.exists() or send_path.is_symlink()
                or _sha256_file(trust_path) != startup_incident["trust_receipt_sha256"]):
            raise AdapterError("pre-submit cleanup lacks the exact delayed-trust incident proof")
    elif authorization_status != "submitting" and state.get("original_request_sha256") is not None:
        raise AdapterError("pre-submit abort refuses any request receipt before transport")
    prompt = None
    if authorization_status == "submitting":
        if prompt_file is None:
            raise AdapterError("submitting-state abort requires the exact prompt file")
        path = _canonical_file(Path(prompt_file), "submitting-state abort prompt")
        prompt = path.read_text(encoding="utf-8").rstrip("\r\n")
        observer = runpy.run_path(str(Path(__file__).resolve().parent / "cortex-desktop-dev"), run_name="phase2_abort_digest")
        if observer["original_request_digest"](prompt) != state.get("original_request_sha256"):
            raise AdapterError("submitting-state abort prompt does not match the sealed request digest")
        finder = namespace.get("native_user_turn_receipts")
        if callable(finder):
            matches = finder(state, prompt)
            if matches:
                raise AdapterError("submitting-state abort found an accepted or ambiguous native user turn")
        elif namespace.get("user_prompt_receipts", lambda *_: 1)(state, prompt) != 0:
            raise AdapterError("submitting-state abort found an accepted native user turn")
    receipt_path = _pre_submit_abort_path(context)
    if receipt_path.exists() or receipt_path.is_symlink():
        raise AdapterError("pre-submit abort authorization has already been used")
    abort_dir = _prepare_evidence_directory(abort_dir_value)
    actions = {
        "status": (), "terminal": (), "capture": ("--lines", "500"),
        "calls": ("--limit", "10000"), "events": ("--lines", "500"), "audit": (),
    }
    filenames = {
        "status": "status.txt", "terminal": "terminal.json", "capture": "capture.txt",
        "calls": "calls.jsonl", "events": "events.jsonl", "audit": "audit.json",
    }
    captured: dict[str, tuple[str, int]] = {}
    for generation in ("", "-final"):
        for action in actions:
            body, exit_code = _invoke_harness_available(namespace, harness, action, actions[action])
            name = action + generation
            captured[name] = (body, exit_code)
            target = abort_dir / f"{Path(filenames[action]).stem}{generation}{Path(filenames[action]).suffix}"
            _atomic_write(target, body)
        try:
            screen = namespace["tmux"]("capture-pane", "-p", "-t", context["tmux_pane_id"], "-S", "-200").stdout
        except (KeyError, OSError, RuntimeError, subprocess.SubprocessError) as exc:
            raise AdapterError("pre-submit abort could not capture the owned rendered screen") from exc
        name = "screen" + generation
        captured[name] = (screen, 0)
        _atomic_write(abort_dir / f"screen{generation}.txt", screen)
        if authorization_status == "submitting":
            finder = namespace.get("native_user_turn_receipts")
            count = len(finder(state, prompt)) if callable(finder) else namespace["user_prompt_receipts"](state, prompt)
            if count != 0:
                raise AdapterError("submitting-state abort observed a native user turn during proof")
    activity = _validate_pre_submit_abort(captured, context, authorization_status, prompt)
    final_context = _session_context(namespace)
    if final_context["session_receipt"] != context["session_receipt"]:
        raise AdapterError("pre-submit abort refused because session ownership changed")
    receipt = {
        "schema_version": PRE_SUBMIT_ABORT_SCHEMA, "status": "authorized",
        "control_record_sha256": control_digest, "arm": arm,
        "session_receipt": context["session_receipt"],
        "authorization_status": authorization_status,
        "startup_incident": control_digest if startup_incident is not None else None,
        "abort_directory": _evidence_directory_identity(abort_dir),
        "activity": activity, "authorized_at": time.time(),
        "artifacts": {
            name: hashlib.sha256(body.encode()).hexdigest()
            for name, (body, _exit_code) in captured.items()
        },
    }
    _atomic_write(receipt_path, json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    _atomic_write(abort_dir / "pre-submit-abort.json", json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    _invoke_harness(namespace, harness, "stop-exact", _exact_stop_arguments(context, interrupt=True))
    receipt["status"] = "consumed"
    receipt["consumed_at"] = time.time()
    _atomic_write(receipt_path, json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    _atomic_write(abort_dir / "pre-submit-abort.json", json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    return receipt


def _invalid_cleanup_failure_matches(failure: dict[str, Any], incident: dict[str, Any],
                                     control_record: Path) -> bool:
    error = str(failure.get("error"))
    control_digest = _sha256_file(control_record)
    return (
        failure.get("schema_version") == EVIDENCE_SCHEMA
        and failure.get("status") == "incomplete"
        and failure.get("completed_actions") == incident["completed_actions"]
        and all(fragment in error for fragment in incident["error_fragments"])
        and (control_digest != MISSING_PROFILES_INVALID_CONTROL_SHA256
             or str(control_record.parent / "plugins/cortex/profiles.json") in error)
    )


def _cleanup_invalid_cell(namespace: dict[str, Any], harness: Path, control_record: Path,
                          arm: str, evidence_value: str, cleanup_value: str,
                          authorization_nonce: str) -> dict[str, Any]:
    """Invalidate one known submitted NO-GO cell and stop only its sealed session."""
    control_digest = _sha256_file(control_record)
    incident = INVALID_CELL_CLEANUP_INCIDENTS.get(control_digest)
    if (incident is None or arm != "baseline"
            or not re.fullmatch(r"[0-9a-f]{64}", authorization_nonce)):
        raise AdapterError("invalid-cell cleanup is limited to the exact authorized NO-GO cell")
    authorization, context = _require_authorization(namespace, control_record, arm, "submitted")
    if authorization.get("cell_id") != "cell-001" or authorization.get("nonce") != authorization_nonce:
        raise AdapterError("invalid-cell cleanup authorization does not match cell-001")
    evidence = _canonical_directory(Path(evidence_value), "incomplete evidence directory")
    if evidence != Path(context["workdir"]).parent / "evidence":
        raise AdapterError("invalid-cell cleanup evidence path is not bound to the cell")
    failure_path = evidence / "collection-failure.json"
    failure, failure_sha256 = _read_owned_json(failure_path, "collection failure receipt")
    if (not _invalid_cleanup_failure_matches(failure, incident, control_record)
            or (evidence / "evidence-bundle.json").exists()):
        raise AdapterError("invalid-cell cleanup lacks the exact observer collection failure")
    preserved_partial_artifacts = {}
    for name, expected_digest in incident.get("partial_artifacts", {}).items():
        path = evidence / name
        if (path.is_symlink() or not path.is_file() or _sha256_file(path) != expected_digest):
            raise AdapterError("invalid-cell cleanup partial evidence is missing or changed")
        preserved_partial_artifacts[name] = expected_digest
    cleanup = _prepare_evidence_directory(cleanup_value)
    actions = {"status": (), "terminal": (), "capture": ("--lines", "500"),
               "calls": ("--limit", "10000"), "events": ("--lines", "500"), "audit": ()}
    captured: dict[str, tuple[str, int]] = {}
    for suffix in ("", "-final"):
        for action, arguments in actions.items():
            captured[action + suffix] = _invoke_harness_available(namespace, harness, action, arguments)
            _atomic_write(cleanup / f"{action}{suffix}.{'jsonl' if action in {'calls','events'} else 'json' if action in {'terminal','audit'} else 'txt'}",
                          captured[action + suffix][0])
        screen = namespace["tmux"]("capture-pane", "-p", "-t", context["tmux_pane_id"], "-S", "-200").stdout
        captured["screen" + suffix] = (screen, 0)
        _atomic_write(cleanup / f"screen{suffix}.txt", screen)
    if any(captured[name] != captured[name + "-final"] for name in (*actions, "screen")):
        raise AdapterError("invalid-cell cleanup refused because terminal or task evidence changed")
    if captured["status"][1] != 0 or captured["status"][0].strip() != "codex dead=0 status=":
        raise AdapterError("invalid-cell cleanup requires the exact idle live Codex pane")
    terminal = json.loads(captured["terminal"][0])
    expected = {"session_id": context["tmux_session_id"], "pane_id": context["tmux_pane_id"],
                "pane_pid": context["tmux_pane_pid"], "current_command": "codex",
                "dead": False, "dead_status": None}
    if any(terminal.get(key) != value for key, value in expected.items()):
        raise AdapterError("invalid-cell cleanup terminal identity is foreign or active")
    _validate_owned_codex_tree(terminal)
    if not namespace["_has_exact_empty_active_composer"](captured["screen"][0]):
        raise AdapterError("invalid-cell cleanup requires the latest exact empty composer")
    root, _ = _validate_inactive_activity(
        captured["calls"][0], captured["audit"][0], allow_terminal_failures=True,
    )
    if root != authorization["submission_native_user_turn"]["thread_id"]:
        raise AdapterError("invalid-cell cleanup task tree does not match the submitted root")
    audit = json.loads(captured["audit"][0])
    invalidators = audit.get("evidence_integrity_invalidators")
    if incident.get("cancelled_by_user"):
        expected = incident["invalidator_identities"]
        actual = [[row.get("violation"), row.get("argument_digest"), row.get("result_digest")]
                  for row in invalidators] if isinstance(invalidators, list) else None
        cancelled_path = _canonical_file(evidence / "audit-cancelled.json", "cancelled audit")
        try:
            cancelled_audit = json.loads(cancelled_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AdapterError("cancelled audit is malformed") from exc
        if (actual != expected or cancelled_audit.get("evidence_integrity_invalidators") != invalidators
                or audit.get("evidence_valid") is not False or audit.get("score_eligible") is not False):
            raise AdapterError("cancelled-cell cleanup integrity invalidators changed or are incomplete")
    receipt = {
        "schema_version": INVALID_CELL_CLEANUP_SCHEMA, "status": "authorized",
        "evidence_valid": False,
        "quality_status": "not_produced", "score_eligible": False,
        "control_record_sha256": control_digest, "arm": arm,
        "cell_id": "cell-001", "session_receipt": context["session_receipt"],
        "authorization_nonce": authorization_nonce,
        "collection_failure_sha256": failure_sha256,
        "evidence_integrity_invalidators": (invalidators if incident.get("cancelled_by_user")
                                            else incident.get("invalidators", [])),
        "cancelled_by_user": bool(incident.get("cancelled_by_user")),
        "cancellation_report": incident.get("cancellation_report"),
        "preserved_partial_artifacts": preserved_partial_artifacts,
        "cleanup_directory": _evidence_directory_identity(cleanup),
        "preserved_diagnostics": {
            "host_failures": len(audit.get("host_failures") or []),
            "policy_violations": len(audit.get("policy_violations") or []),
            "audit_exit_code": captured["audit"][1],
        },
        "artifacts": {name: hashlib.sha256(body.encode()).hexdigest()
                      for name, (body, _exit) in captured.items()},
        "authorized_at": time.time(),
    }
    receipt_path = context["state_root"] / f"phase2-invalid-cell-cleanup-{control_digest}-{context['session_receipt']}.json"
    if receipt_path.exists() or receipt_path.is_symlink():
        raise AdapterError("invalid-cell cleanup authorization has already been used")
    _atomic_write(receipt_path, json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    final_context = _session_context(namespace)
    if final_context["session_receipt"] != context["session_receipt"]:
        raise AdapterError("invalid-cell cleanup session ownership changed")
    _invoke_harness(namespace, harness, "stop-exact", _exact_stop_arguments(final_context, interrupt=True))
    receipt["status"] = "consumed"
    receipt["consumed_at"] = time.time()
    authorization["status"] = "invalidated"
    authorization["invalid_cleanup_receipt"] = str(receipt_path)
    _atomic_write(_authorization_path(context), json.dumps(authorization, indent=2, sort_keys=True) + "\n")
    _atomic_write(receipt_path, json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    _atomic_write(cleanup / "invalid-cell-cleanup.json", json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    return receipt


def _read_orphan_authorization(context: dict[str, Any]) -> dict[str, Any]:
    """Read only the two sealed Phase 2 authorization generations."""
    path = _authorization_path(context)
    if path.is_symlink() or not path.is_file() or stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise AdapterError("orphan recovery requires the exact prior cell authorization receipt")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AdapterError("orphan recovery cell authorization is invalid") from exc
    if not isinstance(value, dict) or value.get("schema_version") != CELL_AUTH_SCHEMA:
        raise AdapterError("orphan recovery cell authorization is invalid")
    return value


def _invoke_harness_available(namespace: dict[str, Any], harness: Path, action: str,
                              arguments: tuple[str, ...]) -> tuple[str, int]:
    """Capture diagnostic stdout even when audit intentionally exits nonzero."""
    stdout, stderr = io.StringIO(), io.StringIO()
    previous = sys.argv
    try:
        sys.argv = [str(harness), action, *arguments]
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = namespace["main"]()
    except SystemExit as exc:
        result = exc.code
    except RuntimeError as exc:
        if stdout.getvalue().strip():
            result = 1
        else:
            detail = stderr.getvalue().strip() or str(exc)
            raise AdapterError(f"orphan recovery {action} capture failed: {detail[:240]}") from exc
    except (OSError, ValueError) as exc:
        detail = stderr.getvalue().strip() or stdout.getvalue().strip() or str(exc)
        raise AdapterError(f"orphan recovery {action} capture failed: {detail[:240]}") from exc
    finally:
        sys.argv = previous
    if stderr.getvalue().strip():
        raise AdapterError(f"orphan recovery {action} capture produced unexpected stderr")
    return stdout.getvalue(), int(result) if isinstance(result, int) else 0


def _validate_inactive_orphan(captured: dict[str, tuple[str, int]], context: dict[str, Any]) -> dict[str, Any]:
    status_body = captured["status"][0].strip()
    status_match = re.fullmatch(r"bash dead=1 status=([0-9]+)", status_body)
    if status_match is None:
        raise AdapterError("orphan recovery requires an exact dead terminal pane; every live pane is refused")
    try:
        terminal = json.loads(captured["terminal"][0])
    except json.JSONDecodeError as exc:
        raise AdapterError("orphan recovery terminal process evidence is malformed") from exc
    expected_terminal = {
        "schema_version": "cortex-live-terminal-snapshot-v1",
        "session_id": context["tmux_session_id"], "pane_id": context["tmux_pane_id"],
        "pane_pid": context["tmux_pane_pid"], "current_command": "bash",
        "dead": True, "dead_status": int(status_match.group(1)), "descendants": [],
    }
    if terminal != expected_terminal:
        raise AdapterError("orphan recovery requires exact owned dead-bash process evidence")
    capture_body = captured["capture"][0]
    marker = re.findall(r"^Cortex live-dev exit=([0-9]+)$", capture_body, flags=re.MULTILINE)
    if marker != [status_match.group(1)]:
        raise AdapterError("orphan recovery requires the matching terminal exit marker")
    calls = _parse_json_lines(captured["calls"][0], "calls") if captured["calls"][0].strip() else []
    events = _parse_json_lines(captured["events"][0], "events") if captured["events"][0].strip() else []
    try:
        audit = json.loads(captured["audit"][0])
    except json.JSONDecodeError as exc:
        raise AdapterError("orphan recovery audit is not a JSON object") from exc
    if not isinstance(audit, dict):
        raise AdapterError("orphan recovery audit is not a JSON object")
    if audit.get("open_sessions") not in ([], None) or audit.get("open_cells") not in ([], None):
        raise AdapterError("orphan recovery refused while an exec session or cell is active")
    if any(
        row.get("outcome") not in TERMINAL_CALL_OUTCOMES
        or row.get("host_status") not in TERMINAL_HOST_STATUSES
        for row in calls
    ):
        raise AdapterError("orphan recovery refused while coordinator, worker, task, exec, or wait activity is active")
    spawned = {
        row.get("spawned_thread_id") for row in calls
        if row.get("tool") == "spawn_agent" and row.get("outcome") == "success"
        and isinstance(row.get("spawned_thread_id"), str)
    }
    completed = {
        row.get("thread_id") for row in calls
        if row.get("tool") == "native_agent_result" and row.get("outcome") == "success"
    }
    if spawned - completed:
        raise AdapterError("orphan recovery refused while a worker is active")
    return {"calls": len(calls), "events": len(events), "audit_exit_code": captured["audit"][1]}


def _recover_orphan(namespace: dict[str, Any], harness: Path, control_record: Path, arm: str,
                    cell_id: str, session_receipt: str, recovery_state: str,
                    authorization_nonce: str, recovery_dir_value: str) -> dict[str, Any]:
    if not re.fullmatch(r"cell-[0-9]{3}", cell_id):
        raise AdapterError("orphan recovery cell id must use cell-NNN format")
    if not re.fullmatch(r"[0-9a-f]{64}", session_receipt):
        raise AdapterError("orphan recovery requires an exact session receipt")
    if not re.fullmatch(r"[0-9a-f]{64}", authorization_nonce):
        raise AdapterError("orphan recovery requires an explicit one-time authorization nonce")
    if recovery_state not in {"failed-pre-submit", "invalidated"}:
        raise AdapterError("orphan recovery state must be failed-pre-submit or invalidated")
    context = _session_context(namespace)
    authorization = _read_orphan_authorization(context)
    expected = {
        "control_record_sha256": _sha256_file(control_record), "arm": arm,
        "cell_id": cell_id, "workdir": context["workdir"], "store": context["store"],
        "started_at": context["started_at"], "thread_created_since": context["thread_created_since"],
        "session_receipt": session_receipt,
    }
    if session_receipt != context["session_receipt"] or any(authorization.get(key) != value for key, value in expected.items()):
        raise AdapterError("orphan recovery control, cell, arm, or session ownership receipt mismatch")
    if authorization.get("status") not in {"issued", "submitting"}:
        raise AdapterError("orphan recovery refuses submitted, collected, consumed, or active authorization state")
    state = namespace["state"]()
    if state.get("first_submission_at") is not None or any(authorization.get(key) is not None for key in (
        "submitted_at", "submission_receipt",
    )):
        raise AdapterError("orphan recovery refuses a submitted session")
    if recovery_state == "failed-pre-submit" and state.get("original_request_sha256") is not None:
        raise AdapterError("failed-pre-submit recovery contradicts the transport request receipt")
    recovery_dir = _prepare_evidence_directory(recovery_dir_value)
    captured: dict[str, tuple[str, int]] = {}
    action_args = {"status": (), "terminal": (), "capture": ("--lines", "500"),
                   "calls": ("--limit", "10000"), "events": ("--lines", "500"), "audit": ()}
    filenames = {"status": "status.txt", "terminal": "terminal.json", "capture": "capture.txt",
                 "calls": "calls.jsonl", "events": "events.jsonl", "audit": "audit.json"}
    for action in ("status", "terminal", "capture", "calls", "events", "audit"):
        body, exit_code = _invoke_harness_available(namespace, harness, action, action_args[action])
        captured[action] = (body, exit_code)
        _atomic_write(recovery_dir / filenames[action], body)
    activity = _validate_inactive_orphan(captured, context)
    recovery_identity = {
        "schema_version": ORPHAN_RECOVERY_SCHEMA, "status": "authorized",
        "authorization_nonce": authorization_nonce, "recovery_state": recovery_state,
        "control_record_sha256": expected["control_record_sha256"], "arm": arm,
        "cell_id": cell_id, "session_receipt": session_receipt,
        "authorization_sha256": _sha256_file(_authorization_path(context)),
        "recovery_directory": _evidence_directory_identity(recovery_dir),
    }
    recovery_state_path = context["state_root"] / "phase2-orphan-recovery.json"
    if recovery_state_path.exists() or recovery_state_path.is_symlink():
        raise AdapterError("orphan recovery authorization has already been used")
    _atomic_write(recovery_state_path, json.dumps(recovery_identity, indent=2, sort_keys=True) + "\n")
    final_context = _session_context(namespace)
    if final_context["session_receipt"] != session_receipt:
        raise AdapterError("orphan recovery session receipt changed before cleanup")
    final_captured: dict[str, tuple[str, int]] = {}
    for action in ("status", "terminal", "capture"):
        final = _invoke_harness_available(namespace, harness, action, action_args[action])
        final_captured[action] = final
        final_name = f"{Path(filenames[action]).stem}-final{Path(filenames[action]).suffix}"
        _atomic_write(recovery_dir / final_name, final[0])
        if final != captured[action]:
            raise AdapterError("orphan recovery terminal status, process, or exit-marker evidence changed before cleanup")
    _invoke_harness(
        namespace, harness, "stop-exact",
        _exact_stop_arguments(final_context, interrupt=False),
    )
    receipt = {
        **recovery_identity, "status": "consumed", "activity": activity,
        "artifacts": {
            action: {"filename": filenames[action], "sha256": _sha256_file(recovery_dir / filenames[action]),
                     "exit_code": captured[action][1]}
            for action in ("status", "terminal", "capture", "calls", "events", "audit")
        },
    }
    for action in ("status", "terminal", "capture"):
        final_name = f"{Path(filenames[action]).stem}-final{Path(filenames[action]).suffix}"
        receipt["artifacts"][f"{action}-final"] = {
            "filename": final_name, "sha256": _sha256_file(recovery_dir / final_name),
            "exit_code": final_captured[action][1],
        }
    _atomic_write(recovery_dir / "orphan-recovery.json", json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    _authorization_path(context).unlink()
    recovery_state_path.unlink()
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--control-record", type=Path)
    parser.add_argument("--arm", choices=("baseline", "candidate"))
    parser.add_argument("command", choices=("preflight", "start", "accept-trust", "begin-cell", "send", "resolve-send", "enter", "status", "capture", "events", "hooks", "calls", "usage", "audit", "collect-evidence", "abort-pre-submit", "cleanup-invalid-cell", "recover-orphan", "gc-absent-runtime", "stop"))
    parsed, remainder = parser.parse_known_args(argv)
    if parsed.command == "gc-absent-runtime":
        if parsed.control_record is not None or parsed.arm is not None:
            raise AdapterError("gc-absent-runtime is independent of a current control or arm")
        gc_parser = argparse.ArgumentParser(prog="phase2_cli_adapter.py gc-absent-runtime", add_help=False)
        gc_parser.add_argument("--state-dir", required=True, type=Path)
        gc_parser.add_argument("--authorization-nonce", required=True)
        try:
            gc = gc_parser.parse_args(remainder)
        except SystemExit as exc:
            raise AdapterError("gc-absent-runtime requires exact state directory and authorization nonce") from exc
        receipt = _gc_absent_runtime(gc.state_dir.resolve(), gc.authorization_nonce)
        receipt_prefix = ("phase2-prebinding-failed-launch-gc-"
                          if receipt.get("schema_version") == PREBINDING_FAILED_LAUNCH_GC_SCHEMA
                          else "phase2-absent-runtime-gc-")
        print(json.dumps({"receipt": str(gc.state_dir / f"{receipt_prefix}{receipt['plan_sha256']}.json"),
                          "status": receipt["status"], "replayed": receipt.get("replayed", False)}, sort_keys=True))
        return 0
    if parsed.control_record is None or parsed.arm is None:
        raise AdapterError("this command requires --control-record and --arm")
    if parsed.command == "cleanup-invalid-cell":
        launcher, harness, observer, arm_root, selected = _load_binding(
            parsed.control_record.resolve(), parsed.arm, invalid_cleanup=True,
        )
    elif parsed.command == "recover-orphan":
        launcher, harness, observer, arm_root, selected = _load_binding(
            parsed.control_record.resolve(), parsed.arm, orphan_recovery=True,
        )
    elif parsed.command == "abort-pre-submit":
        launcher, harness, observer, arm_root, selected = _load_binding(
            parsed.control_record.resolve(), parsed.arm, preauth_cleanup=True,
        )
    else:
        launcher, harness, observer, arm_root, selected = _load_binding(
            parsed.control_record.resolve(), parsed.arm,
        )
    namespace = _load_harness(harness, observer, arm_root, selected)
    namespace["main"].__globals__["PHASE2_CONTROL_SHA256"] = _sha256_file(
        parsed.control_record.resolve(),
    )
    if parsed.command == "preflight":
        if remainder:
            raise AdapterError("preflight accepts no transport arguments")
        print(json.dumps({
            "arm": parsed.arm,
            "effort": launcher["effort"],
            "fresh_store": "required-at-start",
            "mechanism": launcher["mechanism"],
            "model": launcher["model"],
            "status": "pass",
        }, sort_keys=True))
        return 0
    if parsed.command == "start":
        _require_frozen_start(remainder)
        _invoke_harness(namespace, harness, "start", tuple(remainder))
        after, trust = _complete_phase2_startup(namespace, harness)
        print(json.dumps({"arm": parsed.arm, "session_receipt": after["session_receipt"],
                          "status": "ready-for-begin", "trust_status": trust["status"]},
                         sort_keys=True))
        return 0
    if parsed.command == "accept-trust":
        if remainder:
            raise AdapterError("accept-trust accepts no transport arguments")
        before = _session_context(namespace)
        receipt = _require_trust_ready(namespace)
        after = _session_context(namespace)
        if before != after:
            raise AdapterError("owned session receipt changed during trust transition")
        print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
        return 0
    if parsed.command == "begin-cell":
        if len(remainder) != 2 or remainder[0] != "--cell-id":
            raise AdapterError("begin-cell accepts exactly --cell-id cell-NNN")
        _require_trust_ready(namespace)
        authorization = _begin_cell(namespace, parsed.control_record.resolve(), parsed.arm, remainder[1])
        print(json.dumps({
            "arm": parsed.arm,
            "cell_id": authorization["cell_id"],
            "nonce": authorization["nonce"],
            "session_receipt": authorization["session_receipt"],
            "status": "issued",
        }, sort_keys=True))
        return 0
    send_authorization = None
    if parsed.command == "send":
        _invoke_harness(namespace, harness, "send-preflight", tuple(remainder))
        _mark_submitting(namespace, parsed.control_record.resolve(), parsed.arm)
        send_authorization = True
    if parsed.command == "resolve-send":
        _require_authorization(namespace, parsed.control_record.resolve(), parsed.arm, "submitting")
        body = _invoke_harness(namespace, harness, "resolve-send", tuple(remainder))
        _mark_submitted(namespace, parsed.control_record.resolve(), parsed.arm)
        print(body, end="")
        return 0
    if parsed.command == "collect-evidence":
        evidence_parser = argparse.ArgumentParser(prog="phase2_cli_adapter.py collect-evidence", add_help=False)
        evidence_parser.add_argument("--evidence-dir", required=True)
        if len(remainder) != 2 or remainder[0] != "--evidence-dir":
            raise AdapterError("collect-evidence accepts exactly --evidence-dir PATH")
        try:
            evidence = evidence_parser.parse_args(remainder)
        except SystemExit as exc:
            raise AdapterError("collect-evidence accepts exactly --evidence-dir PATH") from exc
        authorization, context = _require_authorization(
            namespace, parsed.control_record.resolve(), parsed.arm, "submitted",
        )
        directory = _prepare_evidence_directory(evidence.evidence_dir)
        _collect_evidence(
            namespace, harness, directory, parsed.control_record.resolve(), parsed.arm,
            authorization, context,
        )
        print(json.dumps({
            "arm": parsed.arm, "cell_id": authorization["cell_id"],
            "evidence_dir": str(directory), "status": "complete",
        }, sort_keys=True))
        return 0
    if parsed.command == "abort-pre-submit":
        abort_parser = argparse.ArgumentParser(prog="phase2_cli_adapter.py abort-pre-submit", add_help=False)
        abort_parser.add_argument("--abort-dir", required=True)
        abort_parser.add_argument("--prompt-file")
        try:
            abort = abort_parser.parse_args(remainder)
        except SystemExit as exc:
            raise AdapterError("abort-pre-submit accepts --abort-dir PATH and optional --prompt-file PATH") from exc
        receipt = _abort_pre_submit(
            namespace, harness, parsed.control_record.resolve(), parsed.arm, abort.abort_dir,
            abort.prompt_file,
        )
        print(json.dumps({
            "arm": parsed.arm, "abort_dir": abort.abort_dir, "status": receipt["status"],
        }, sort_keys=True))
        return 0
    if parsed.command == "cleanup-invalid-cell":
        cleanup_parser = argparse.ArgumentParser(prog="phase2_cli_adapter.py cleanup-invalid-cell", add_help=False)
        cleanup_parser.add_argument("--evidence-dir", required=True)
        cleanup_parser.add_argument("--cleanup-dir", required=True)
        cleanup_parser.add_argument("--authorization-nonce", required=True)
        try:
            cleanup = cleanup_parser.parse_args(remainder)
        except SystemExit as exc:
            raise AdapterError("cleanup-invalid-cell requires exact evidence, cleanup, and nonce arguments") from exc
        receipt = _cleanup_invalid_cell(
            namespace, harness, parsed.control_record.resolve(), parsed.arm,
            cleanup.evidence_dir, cleanup.cleanup_dir, cleanup.authorization_nonce,
        )
        print(json.dumps({"arm": parsed.arm, "cell_id": "cell-001",
                          "cleanup_dir": cleanup.cleanup_dir, "status": receipt["status"],
                          "quality_status": receipt["quality_status"]}, sort_keys=True))
        return 0
    if parsed.command == "recover-orphan":
        recovery_parser = argparse.ArgumentParser(prog="phase2_cli_adapter.py recover-orphan", add_help=False)
        recovery_parser.add_argument("--cell-id", required=True)
        recovery_parser.add_argument("--session-receipt", required=True)
        recovery_parser.add_argument("--recovery-state", required=True)
        recovery_parser.add_argument("--authorization-nonce", required=True)
        recovery_parser.add_argument("--recovery-dir", required=True)
        try:
            recovery = recovery_parser.parse_args(remainder)
        except SystemExit as exc:
            raise AdapterError("recover-orphan requires exact cell, session, state, nonce, and directory arguments") from exc
        receipt = _recover_orphan(
            namespace, harness, parsed.control_record.resolve(), parsed.arm,
            recovery.cell_id, recovery.session_receipt, recovery.recovery_state,
            recovery.authorization_nonce, recovery.recovery_dir,
        )
        print(json.dumps({
            "arm": parsed.arm, "cell_id": recovery.cell_id,
            "recovery_dir": recovery.recovery_dir, "status": receipt["status"],
        }, sort_keys=True))
        return 0
    stop_authorization = None
    stop_context = None
    if parsed.command == "stop":
        stop_parser = argparse.ArgumentParser(prog="phase2_cli_adapter.py stop", add_help=False)
        stop_parser.add_argument("--evidence-dir", required=True)
        stop_parser.add_argument("--interrupt", action="store_true")
        if len(remainder) not in {2, 3} or remainder[0] != "--evidence-dir" or (len(remainder) == 3 and remainder[2] != "--interrupt"):
            raise AdapterError("stop requires --evidence-dir PATH and optionally --interrupt")
        try:
            stop = stop_parser.parse_args(remainder)
        except SystemExit as exc:
            raise AdapterError("stop requires --evidence-dir PATH and optionally --interrupt") from exc
        stop_directory, stop_authorization, stop_context = _validate_evidence_bundle(
            stop.evidence_dir, parsed.control_record.resolve(), parsed.arm, namespace,
        )
        _revalidate_terminal_before_stop(namespace, harness, stop_directory, stop_context)
        remainder = list(_exact_stop_arguments(stop_context, interrupt=stop.interrupt))
    dispatch_command = "stop-exact" if parsed.command == "stop" else parsed.command
    previous = sys.argv
    try:
        sys.argv = [str(harness), dispatch_command, *remainder]
        result = namespace["main"]()
    finally:
        sys.argv = previous
    exit_code = int(result) if isinstance(result, int) else 0
    if exit_code != 0:
        return exit_code
    if parsed.command == "send" and send_authorization is not None:
        _mark_submitted(namespace, parsed.control_record.resolve(), parsed.arm)
    if parsed.command == "stop" and stop_authorization is not None and stop_context is not None:
        stop_authorization["status"] = "consumed"
        _atomic_write(
            _authorization_path(stop_context),
            json.dumps(stop_authorization, indent=2, sort_keys=True) + "\n",
        )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AdapterError, OSError, ValueError) as exc:
        raise SystemExit("Phase 2 live adapter refused: " + str(exc)) from None
