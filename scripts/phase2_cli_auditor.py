#!/usr/bin/env python3
"""Independent offline auditor for a ``phase2-cli-v1`` control record.

The auditor validates only immutable preparation evidence.  It never launches
an agent, opens a provider, touches a listener, reads fixture trees, or scores
an observation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import stat
import subprocess
import sys
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
FAMILIES = {f"F-0{i}" for i in range(1, 7)}
FORBIDDEN_NEUTRAL_KEYS = {"arm", "condition", "baseline", "candidate", "true_condition"}
REQUIRED_MANIFEST_FIELDS = (
    "task_id", "family", "prompt_sha256", "fixture_tree_sha256",
    "oracle_sha256", "reset_sha256", "oracle_path", "reset_path",
    "reset_policy", "protected_paths", "oracle_command", "reset_command",
)
REQUIRED_TOOLS = (
    "create_task", "set_governance", "create_draft", "read_draft",
    "write_report", "list_reports", "read_report",
)
UNSAFE_ENVIRONMENT_KEYS = {
    "PYTHONPATH", "PYTHONHOME", "CORTEX_DATA_DIR", "CODEX_HOME",
    "CORTEX_OBSERVATION_DIR", "CORTEX_DEPENDENCY_DIR", "HTTP_PROXY",
    "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "http_proxy", "https_proxy",
    "all_proxy", "no_proxy", "LD_PRELOAD", "LD_AUDIT", "PYTHONINSPECT",
    "PYTHONSTARTUP", "PYTHONWARNINGS", "PYTHONPYCACHEPREFIX",
}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_sha256(path: Path) -> str:
    return sha256(path.read_bytes())


def verify_observer_dependencies(record_root: Path, launcher: dict[str, Any],
                                 errors: list[str]) -> None:
    """Independently verify the observer's exact sealed runtime file closure."""
    manifest = launcher.get("observer_dependencies")
    if (launcher.get("observer_bundle_root") != str(record_root)
            or launcher.get("observer_dependencies_sha256")
            != TRUSTED_OBSERVER_DEPENDENCIES_SHA256 or not isinstance(manifest, dict)):
        fail(errors, "common observer dependency binding is malformed")
        return
    required = "plugins/cortex/profiles.json"
    if (not manifest or required not in manifest
            or not any(isinstance(key, str) and key.startswith("plugins/cortex/skills/")
                       for key in manifest)):
        fail(errors, "common observer dependency closure is incomplete")
        return
    dependency_root = record_root / "plugins/cortex"
    actual = set()
    if dependency_root.is_symlink() or not dependency_root.is_dir():
        fail(errors, "common observer dependency root is unavailable")
        return
    for path in dependency_root.rglob("*"):
        if path.is_symlink():
            fail(errors, "common observer dependency contains a symlink")
            continue
        if path.is_file():
            actual.add(path.relative_to(record_root).as_posix())
    if actual != set(manifest):
        fail(errors, "common observer dependency closure does not match its manifest")
    valid_manifest = True
    for relative, digest in manifest.items():
        path = Path(relative) if isinstance(relative, str) else Path("__invalid__")
        if (not isinstance(relative, str) or not hex64(digest) or path.is_absolute()
                or ".." in path.parts
                or not (relative == required or relative.startswith("plugins/cortex/skills/"))):
            fail(errors, "common observer dependency manifest is malformed")
            valid_manifest = False
            continue
        target = record_root / path
        if (not target.is_absolute() or target.is_symlink() or not target.is_file()
                or target.resolve() != target or target.stat().st_uid != os.getuid()
                or stat.S_IMODE(target.stat().st_mode) != 0o600):
            fail(errors, "common observer dependency is unavailable or unsafe")
        elif file_sha256(target) != digest:
            fail(errors, "common observer dependency digest mismatch")
    if valid_manifest:
        aggregate = sha256(json.dumps(
            manifest, sort_keys=True, separators=(",", ":"),
        ).encode())
        if aggregate != TRUSTED_OBSERVER_DEPENDENCIES_SHA256:
            fail(errors, "common observer dependency aggregate mismatch")


def hex64(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and set(value) <= HEX64


def safe_relative_path(value: Any) -> bool:
    if not isinstance(value, str) or not value or value != value.strip() or "\\" in value or "\x00" in value:
        return False
    path = Path(value)
    return not path.is_absolute() and all(part not in {"", ".", ".."} for part in path.parts)


def fail(errors: list[str], message: str) -> None:
    errors.append(message)


def object_or_empty(value: Any, label: str, errors: list[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        fail(errors, f"{label} is not an object")
        return {}
    return value


def exact_object(value: Any, label: str, keys: set[str], errors: list[str]) -> dict[str, Any]:
    """Require a closed object shape before any nested field is trusted."""
    if not isinstance(value, dict):
        fail(errors, f"{label} is not an object")
        return {}
    result = value
    missing = sorted(keys - set(result))
    extra = sorted(set(result) - keys)
    if missing:
        fail(errors, f"{label} missing fields: {', '.join(missing)}")
    if extra:
        fail(errors, f"{label} has unknown fields: {', '.join(extra)}")
    return result


def require_type(value: Any, expected: type | tuple[type, ...], label: str, errors: list[str]) -> None:
    if not isinstance(value, expected):
        fail(errors, f"{label} has invalid type")


def validate_nested_schemas(record: dict[str, Any], errors: list[str]) -> None:
    """Validate every generated section, including explicit reason fields."""
    exact_object(record, "control record", {"schema_version", "suite", "status", "manifest_path", "neutral", "identities", "arms", "live_activity"}, errors)
    identities = exact_object(record.get("identities"), "identities", {"baseline", "candidate"}, errors)
    exact_object(identities.get("baseline"), "identities.baseline", {"commit", "version", "payload_sha256"}, errors)
    exact_object(identities.get("candidate"), "identities.candidate", {"version", "source_commit", "payload_sha256"}, errors)

    neutral = exact_object(record.get("neutral"), "neutral", {"suite", "host", "evaluator", "live_launcher", "interpreter_dependencies", "model", "provider", "tools", "task", "timeout_wait", "diagnostics", "environment", "evidence_schema"}, errors)
    host = exact_object(neutral.get("host"), "neutral.host", {"host_id_sha256", "python", "git", "tmux", "codex"}, errors)
    python = exact_object(host.get("python"), "neutral.host.python", {"status", "version", "executable_sha256"}, errors)
    if python.get("status") != "observed" or not isinstance(python.get("version"), str) or not hex64(python.get("executable_sha256")):
        fail(errors, "neutral.host.python is malformed")
    if not hex64(host.get("host_id_sha256")):
        fail(errors, "neutral.host.host_id_sha256 is malformed")
    for name in ("git", "tmux", "codex"):
        tool = exact_object(host.get(name), f"neutral.host.{name}", {"status", "reason", "version"}, errors)
        require_type(tool.get("status"), str, f"neutral.host.{name}.status", errors)
        require_type(tool.get("reason"), (str, type(None)), f"neutral.host.{name}.reason", errors)
        require_type(tool.get("version"), (str, type(None)), f"neutral.host.{name}.version", errors)
    evaluator = exact_object(neutral.get("evaluator"), "neutral.evaluator", {"runner_sha256", "auditor_sha256", "preparer_sha256"}, errors)
    if not all(hex64(evaluator.get(key)) for key in ("runner_sha256", "auditor_sha256", "preparer_sha256")):
        fail(errors, "neutral.evaluator hashes are malformed")
    launcher = exact_object(neutral.get("live_launcher"), "neutral.live_launcher", {"mechanism", "adapter_path", "adapter_sha256", "harness_path", "harness_sha256", "observer_path", "observer_sha256", "observer_bundle_root", "observer_dependencies", "observer_dependencies_sha256", "fresh_store", "model", "effort", "evidence_collection"}, errors)
    if launcher.get("mechanism") != "phase2-common-live-harness-v1" or launcher.get("fresh_store") != "required-at-start" or launcher.get("model") != "gpt-5.6-luna" or launcher.get("effort") != "high" or not all(hex64(launcher.get(key)) for key in ("adapter_sha256", "harness_sha256", "observer_sha256", "observer_dependencies_sha256")):
        fail(errors, "neutral.live_launcher is malformed")
    if launcher.get("evidence_collection") != EVIDENCE_COLLECTION:
        fail(errors, "sealed evidence-collection contract mismatch")
    dependencies = exact_object(neutral.get("interpreter_dependencies"), "neutral.interpreter_dependencies", {"route", "lock_sha256", "installed_tree_sha256", "combined_dependency_sha256", "python_executable_sha256"}, errors)
    installed = exact_object(dependencies.get("installed_tree_sha256"), "neutral.interpreter_dependencies.installed_tree_sha256", {"status", "sha256", "reason"}, errors)
    require_type(installed.get("reason"), str, "neutral.interpreter_dependencies.installed_tree_sha256.reason", errors)
    if dependencies.get("route") != "external-hash-locked-file" or not all(hex64(dependencies.get(key)) for key in ("lock_sha256", "combined_dependency_sha256", "python_executable_sha256")):
        fail(errors, "neutral.interpreter_dependencies is malformed")
    if installed.get("status") != "unavailable" or installed.get("sha256") is not None:
        fail(errors, "neutral.interpreter_dependencies.installed_tree_sha256 is malformed")
    model = exact_object(neutral.get("model"), "neutral.model", {"coordinator", "coordinator_effort", "worker", "worker_effort", "consultant"}, errors)
    if not all(isinstance(model.get(key), str) for key in ("coordinator", "coordinator_effort", "worker", "worker_effort", "consultant")):
        fail(errors, "neutral.model is malformed")
    provider = exact_object(neutral.get("provider"), "neutral.provider", {"route", "route_sha256", "status"}, errors)
    if not isinstance(provider.get("route"), str) or not hex64(provider.get("route_sha256")) or not isinstance(provider.get("status"), str):
        fail(errors, "neutral.provider is malformed")
    tools = exact_object(neutral.get("tools"), "neutral.tools", {"mcp_names", "apps", "codebase_memory", "node_repl", "capabilities_sha256"}, errors)
    if tools.get("mcp_names") != list(REQUIRED_TOOLS) or not hex64(tools.get("capabilities_sha256")):
        fail(errors, "neutral.tools is malformed")
    for name in ("apps", "codebase_memory", "node_repl"):
        capability = exact_object(tools.get(name), f"neutral.tools.{name}", {"status", "reason"}, errors)
        require_type(capability.get("reason"), str, f"neutral.tools.{name}.reason", errors)
    task = exact_object(neutral.get("task"), "neutral.task", {"manifest_sha256", "prompt_sha256_by_family", "fixture_tree_sha256_by_family", "oracle_sha256_by_family", "reset_sha256_by_family", "fixture_access"}, errors)
    if not hex64(task.get("manifest_sha256")) or not isinstance(task.get("fixture_access"), str):
        fail(errors, "neutral.task is malformed")
    for field in ("prompt_sha256_by_family", "fixture_tree_sha256_by_family", "oracle_sha256_by_family", "reset_sha256_by_family"):
        mapping = task.get(field)
        if not isinstance(mapping, dict):
            fail(errors, f"neutral.task.{field} is not an object")
    timeout_wait = exact_object(neutral.get("timeout_wait"), "neutral.timeout_wait", {"submit_delay_seconds", "submit_receipt_poll", "cell_timeout_seconds", "wait_timeout_ms", "timeout_is_terminal"}, errors)
    if not isinstance(timeout_wait.get("submit_delay_seconds"), int) or not isinstance(timeout_wait.get("submit_receipt_poll"), str) or not isinstance(timeout_wait.get("cell_timeout_seconds"), int) or not isinstance(timeout_wait.get("wait_timeout_ms"), int) or not isinstance(timeout_wait.get("timeout_is_terminal"), bool):
        fail(errors, "neutral.timeout_wait is malformed")
    diagnostics = exact_object(neutral.get("diagnostics"), "neutral.diagnostics", {"codebase_memory", "git_rg", "network_dependent_oracle", "private_cortex_mcp_lifecycle_transport", "raw_logs"}, errors)
    if not all(isinstance(diagnostics.get(key), str) for key in ("codebase_memory", "git_rg", "private_cortex_mcp_lifecycle_transport", "raw_logs")) or not isinstance(diagnostics.get("network_dependent_oracle"), bool):
        fail(errors, "neutral.diagnostics is malformed")
    environment = exact_object(neutral.get("environment"), "neutral.environment", {"allowlist_sha256", "allowlist_keys", "ambient_paths", "proxy_policy", "umask", "unsafe_environment_keys"}, errors)
    if not hex64(environment.get("allowlist_sha256")) or not isinstance(environment.get("allowlist_keys"), list) or not all(isinstance(key, str) for key in environment.get("allowlist_keys", [])) or not all(isinstance(environment.get(key), str) for key in ("ambient_paths", "proxy_policy", "umask")) or not isinstance(environment.get("unsafe_environment_keys"), dict):
        fail(errors, "neutral.environment is malformed")
    evidence = exact_object(neutral.get("evidence_schema"), "neutral.evidence_schema", {"control", "terminal", "audit", "opaque_run_id", "quality_separate_from_diagnostics"}, errors)
    if evidence.get("control") != SCHEMA or evidence.get("terminal") != "phase2-terminal-v1" or evidence.get("audit") != "phase2-audit-v1" or not isinstance(evidence.get("opaque_run_id"), str) or not isinstance(evidence.get("quality_separate_from_diagnostics"), bool):
        fail(errors, "neutral.evidence_schema is malformed")
    if isinstance(environment.get("allowlist_keys"), list) and any(key in UNSAFE_ENVIRONMENT_KEYS for key in environment["allowlist_keys"]):
        fail(errors, "sanitized environment allowlist overlaps denied keys")

    arms = record.get("arms")
    if not isinstance(arms, list):
        return
    for index, arm_value in enumerate(arms):
        arm = exact_object(arm_value, f"arms[{index}]", {"arm", "identity", "workdir", "workdir_sha256", "checkout", "fresh_store", "reset", "terminal_receipt", "audit_receipt", "transport_files", "live_launcher_mechanism", "control_neutral_fingerprint"}, errors)
        name = arm.get("arm", f"arms[{index}]")
        exact_object(arm.get("identity"), f"{name}.identity", {"version", "baseline_commit", "source_commit", "payload_sha256"}, errors)
        exact_object(arm.get("fresh_store"), f"{name}.fresh_store", {"status", "relative_path", "sha256"}, errors)
        transport = exact_object(arm.get("transport_files"), f"{name}.transport_files", {"launcher", "observer"}, errors)
        for transport_name in ("launcher", "observer"):
            exact_object(transport.get(transport_name), f"{name}.transport_files.{transport_name}", {"relative_path", "sha256", "mode"}, errors)
        reset = exact_object(arm.get("reset"), f"{name}.reset", {"status", "manifest_sha256", "reason"}, errors)
        require_type(reset.get("reason"), str, f"{name}.reset.reason", errors)
        for receipt_name in ("terminal_receipt", "audit_receipt"):
            receipt = exact_object(arm.get(receipt_name), f"{name}.{receipt_name}", {"schema", "status", "exit_code", "open_receipt", "duplicate_receipt", "normalization", "reason"}, errors)
            require_type(receipt.get("reason"), str, f"{name}.{receipt_name}.reason", errors)

    exact_object(record.get("live_activity"), "live_activity", {"status", "codex_invoked", "listener_touched", "network_used", "score_or_reveal"}, errors)


def trusted_source_commit(errors: list[str]) -> str | None:
    """Read the auditor checkout's HEAD as an external provenance anchor."""
    repo = Path(__file__).resolve().parents[1]
    result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=False)
    commit = result.stdout.strip()
    if result.returncode != 0 or not re.fullmatch(r"[0-9a-f]{40}", commit):
        fail(errors, "candidate source provenance is unavailable")
        return None
    return commit


def validate_manifest_row(row: Any, expected_task: str, errors: list[str]) -> None:
    if not isinstance(row, dict):
        fail(errors, f"manifest {expected_task} is not an object")
        return
    for key in REQUIRED_MANIFEST_FIELDS:
        if key not in row:
            fail(errors, f"manifest {expected_task} missing {key}")
    if row.get("task_id") != expected_task:
        fail(errors, f"manifest task mapping is invalid for {expected_task}")
    if not isinstance(row.get("family"), str) or not row["family"].strip():
        fail(errors, f"manifest {expected_task} has invalid family")
    for field in ("prompt_sha256", "fixture_tree_sha256", "oracle_sha256", "reset_sha256"):
        if not hex64(row.get(field)):
            fail(errors, f"manifest {expected_task} has invalid {field}")
    if row.get("oracle_path") != "oracle.py" or row.get("reset_path") != "reset.py":
        fail(errors, f"manifest {expected_task} has remapped oracle/reset path")
    if row.get("oracle_command") != "python3 oracle.py TASK_ID WORKTREE":
        fail(errors, f"manifest {expected_task} has remapped oracle command")
    if row.get("reset_command") != "python3 reset.py --task TASK_ID --destination WORKTREE":
        fail(errors, f"manifest {expected_task} has remapped reset command")
    policy = row.get("reset_policy")
    if not isinstance(policy, str) or not policy.strip() or "absent" not in policy.casefold() or "never overwrit" not in policy.casefold():
        fail(errors, f"manifest {expected_task} has unsafe reset policy")
    protected = row.get("protected_paths")
    if not isinstance(protected, list) or not protected or not all(safe_relative_path(path) for path in protected) or "USER-NOTE.txt" not in protected:
        fail(errors, f"manifest {expected_task} has invalid protected paths")


def real_private_dir(path: Path, errors: list[str]) -> bool:
    if not path.is_absolute() or path.resolve() != path:
        fail(errors, f"non-canonical workdir: {path}")
        return False
    if path.is_symlink() or not path.is_dir():
        fail(errors, f"workdir is not a real directory: {path}")
        return False
    info = path.stat()
    if info.st_uid != os.getuid() or info.st_mode & 0o077:
        fail(errors, f"workdir is not owner-private: {path}")
        return False
    return True


def payload_digest(plugin: Path, canonical_version: str) -> str:
    if not plugin.is_dir() or plugin.is_symlink():
        raise ValueError(f"missing payload: {plugin}")
    for parent, directories, files in os.walk(plugin, followlinks=False):
        if any((Path(parent) / name).is_symlink() for name in (*directories, *files)):
            raise ValueError("symlink in payload")
    digest = hashlib.sha256()
    for path in sorted(plugin.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix in {".pyc", ".pyo"} or "__pycache__" in path.parts:
            raise ValueError("bytecode in payload")
        rel = path.relative_to(plugin).as_posix()
        body = path.read_bytes()
        if rel == ".codex-plugin/plugin.json":
            value = json.loads(body)
            value["version"] = canonical_version
            body = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        digest.update(rel.encode() + b"\0" + str(len(body)).encode() + b"\0" + body)
    return digest.hexdigest()


def payload_version(plugin: Path) -> str:
    manifest = plugin / ".codex-plugin" / "plugin.json"
    if manifest.is_symlink() or not manifest.is_file():
        raise ValueError("missing plugin identity file")
    value = json.loads(manifest.read_bytes())
    version = value.get("version") if isinstance(value, dict) else None
    if not isinstance(version, str):
        raise ValueError("invalid plugin identity version")
    return version


def neutral_fingerprint(neutral: dict[str, Any]) -> str:
    return sha256(json.dumps(neutral, sort_keys=True, separators=(",", ":")).encode())


def inspect_neutral(value: Any, prefix: str, errors: list[str]) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in FORBIDDEN_NEUTRAL_KEYS:
                fail(errors, f"condition-specific neutral key: {prefix}.{key}")
            inspect_neutral(child, f"{prefix}.{key}", errors)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            inspect_neutral(child, f"{prefix}[{index}]", errors)


def _audit_impl(record_path: Path) -> dict[str, Any]:
    errors: list[str] = []
    try:
        record = json.loads(record_path.read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {"suite": SUITE, "status": "fail-closed", "errors": [f"record unreadable: {exc}"]}
    if not isinstance(record, dict):
        return {"suite": SUITE, "status": "fail-closed", "errors": ["record is not an object"]}
    try:
        record_info = record_path.stat()
        if record_path.is_symlink() or record_info.st_uid != os.getuid() or record_info.st_mode & 0o777 != 0o400:
            fail(errors, "control record is not owner-read-only")
    except OSError:
        fail(errors, "control record metadata is unavailable")
    if record.get("schema_version") != SCHEMA or record.get("suite") != SUITE:
        fail(errors, "control schema or suite mismatch")
    if record.get("status") != "prepared-offline":
        fail(errors, "record is not an offline preparation record")
    validate_nested_schemas(record, errors)
    neutral = record.get("neutral")
    if not isinstance(neutral, dict):
        fail(errors, "neutral envelope missing")
        neutral = {}
    inspect_neutral(neutral, "neutral", errors)
    identities = object_or_empty(record.get("identities"), "identities", errors)
    baseline_identity = identities.get("baseline")
    candidate_identity = object_or_empty(identities.get("candidate"), "identities.candidate", errors)
    candidate_source_commit = candidate_identity.get("source_commit")
    trusted_commit = trusted_source_commit(errors)
    if baseline_identity != {
        "commit": BASELINE_COMMIT, "version": BASELINE_VERSION, "payload_sha256": BASELINE_PAYLOAD_SHA256
    } or candidate_identity.get("version") != CANDIDATE_VERSION or candidate_identity.get("payload_sha256") != CANDIDATE_PAYLOAD_SHA256 or not isinstance(candidate_source_commit, str) or len(candidate_source_commit) != 40 or not set(candidate_source_commit) <= set("0123456789abcdef") or trusted_commit is None or candidate_source_commit != trusted_commit:
        fail(errors, "full identity lock mismatch")
    manifest_value = record.get("manifest_path")
    manifest_path = Path(manifest_value) if isinstance(manifest_value, str) else Path("__invalid_manifest_path__")
    if not manifest_path.is_absolute() or manifest_path.is_symlink() or not manifest_path.is_file():
        fail(errors, "fixture manifest path is unavailable")
    else:
        try:
            manifest = json.loads(manifest_path.read_bytes())
            families = manifest.get("families", []) if isinstance(manifest, dict) else []
            if not isinstance(manifest, dict) or manifest.get("suite_version") != SUITE or not isinstance(families, list) or len(families) != 6 or {row.get("task_id") for row in families if isinstance(row, dict)} != FAMILIES:
                fail(errors, "fixture manifest identity mismatch")
            task = object_or_empty(neutral.get("task"), "neutral.task", errors)
            if task.get("manifest_sha256") != file_sha256(manifest_path):
                fail(errors, "fixture manifest digest mismatch")
            for index, row in enumerate(families, start=1):
                validate_manifest_row(row, f"F-{index:02d}", errors)
            if isinstance(task, dict):
                expected_maps = {
                    "prompt_sha256_by_family": {row["task_id"]: row["prompt_sha256"] for row in families if isinstance(row, dict) and "task_id" in row and "prompt_sha256" in row},
                    "fixture_tree_sha256_by_family": {row["task_id"]: row["fixture_tree_sha256"] for row in families if isinstance(row, dict) and "task_id" in row and "fixture_tree_sha256" in row},
                    "oracle_sha256_by_family": {row["task_id"]: row["oracle_sha256"] for row in families if isinstance(row, dict) and "task_id" in row and "oracle_sha256" in row},
                    "reset_sha256_by_family": {row["task_id"]: row["reset_sha256"] for row in families if isinstance(row, dict) and "task_id" in row and "reset_sha256" in row},
                }
                for field, expected in expected_maps.items():
                    if task.get(field) != expected:
                        fail(errors, f"task hash map mismatch: {field}")
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, AttributeError, KeyError, TypeError):
            fail(errors, "fixture manifest is invalid")
    model = object_or_empty(neutral.get("model"), "neutral.model", errors)
    if model.get("coordinator") != "gpt-5.6-luna" or model.get("coordinator_effort") != "high" or model.get("worker") != "gpt-5.6-luna" or model.get("worker_effort") not in {"medium", "high"} or model.get("consultant") != "disabled":
        fail(errors, "model/effort contract is not Luna-only")
    provider = object_or_empty(neutral.get("provider"), "neutral.provider", errors)
    if provider.get("status") != "declared-common-offline-contract" or not isinstance(provider.get("route"), str) or not provider["route"].startswith("offline://") or not hex64(provider.get("route_sha256")) or provider.get("route_sha256") != sha256(provider["route"].encode()):
        fail(errors, "provider route contract missing")
    deps = object_or_empty(neutral.get("interpreter_dependencies"), "neutral.interpreter_dependencies", errors)
    if not all(hex64(deps.get(key)) for key in ("python_executable_sha256", "lock_sha256", "combined_dependency_sha256")):
        fail(errors, "dependency lock identity missing")
    installed_tree = object_or_empty(deps.get("installed_tree_sha256"), "neutral.interpreter_dependencies.installed_tree_sha256", errors)
    if installed_tree.get("status") != "unavailable" or not installed_tree.get("reason") or installed_tree.get("sha256") is not None:
        fail(errors, "installed dependency status is not explicit")
    diagnostics = object_or_empty(neutral.get("diagnostics"), "neutral.diagnostics", errors)
    if diagnostics.get("private_cortex_mcp_lifecycle_transport") != "hard-stop" or diagnostics.get("network_dependent_oracle") is not False:
        fail(errors, "diagnostic isolation contract missing")
    environment = object_or_empty(neutral.get("environment"), "neutral.environment", errors)
    unsafe_keys = UNSAFE_ENVIRONMENT_KEYS
    allowlist_keys = environment.get("allowlist_keys")
    if environment.get("ambient_paths") != "unset" or environment.get("proxy_policy") != "blocked" or environment.get("umask") != "077" or not hex64(environment.get("allowlist_sha256")) or not isinstance(allowlist_keys, list) or not allowlist_keys or any(not isinstance(key, str) for key in allowlist_keys) or len(set(allowlist_keys)) != len(allowlist_keys) or not all(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) for key in allowlist_keys) or any(key in unsafe_keys for key in allowlist_keys):
        fail(errors, "sanitized environment contract missing")
    if environment.get("unsafe_environment_keys") != {key: "absent" for key in sorted(unsafe_keys)}:
        fail(errors, "unsafe environment denylist is not explicit")
    task = object_or_empty(neutral.get("task"), "neutral.task", errors)
    for field in ("prompt_sha256_by_family", "fixture_tree_sha256_by_family", "oracle_sha256_by_family", "reset_sha256_by_family"):
        mapping = task.get(field)
        if not isinstance(mapping, dict) or set(mapping) != FAMILIES or not all(hex64(v) for v in mapping.values()):
            fail(errors, f"task hash map missing: {field}")
    evaluator = object_or_empty(neutral.get("evaluator"), "neutral.evaluator", errors)
    runner_path = Path(__file__).with_name("phase2_cli_runner.py")
    runner_digest = file_sha256(runner_path)
    if evaluator.get("runner_sha256") != runner_digest or evaluator.get("preparer_sha256") != runner_digest:
        fail(errors, "runner/preparer digest mismatch")
    auditor_path = Path(__file__).resolve()
    if evaluator.get("auditor_sha256") != file_sha256(auditor_path):
        fail(errors, "auditor digest mismatch")
    launcher = object_or_empty(neutral.get("live_launcher"), "neutral.live_launcher", errors)
    adapter_value = launcher.get("adapter_path")
    harness_value = launcher.get("harness_path")
    observer_value = launcher.get("observer_path")
    adapter_path = Path(adapter_value) if isinstance(adapter_value, str) else Path("__invalid_adapter__")
    harness_path = Path(harness_value) if isinstance(harness_value, str) else Path("__invalid_harness__")
    observer_path = Path(observer_value) if isinstance(observer_value, str) else Path("__invalid_observer__")
    expected_harness_dir = record_path.parent / "harness"
    expected_adapter_path = Path(__file__).resolve().with_name("phase2_cli_adapter.py")
    if (adapter_path != expected_adapter_path or harness_path != expected_harness_dir / "cortex-live-smoke"
            or observer_path != expected_harness_dir / "cortex-desktop-dev"):
        fail(errors, "common live-launcher path binding mismatch")
    if (launcher.get("adapter_sha256") != TRUSTED_ADAPTER_SHA256
            or launcher.get("harness_sha256") != TRUSTED_HARNESS_SHA256
            or launcher.get("observer_sha256") != TRUSTED_OBSERVER_SHA256):
        fail(errors, "common live-launcher trusted digest mismatch")
    for label, path, digest in (
        ("adapter", adapter_path, launcher.get("adapter_sha256")),
        ("harness", harness_path, launcher.get("harness_sha256")),
        ("observer", observer_path, launcher.get("observer_sha256")),
    ):
        if not path.is_absolute() or path.is_symlink() or not path.is_file() or path.resolve() != path:
            fail(errors, f"common live {label} is unavailable")
        elif file_sha256(path) != digest:
            fail(errors, f"common live {label} digest mismatch")
    verify_observer_dependencies(record_path.parent, launcher, errors)
    arms = record.get("arms")
    if not isinstance(arms, list) or len(arms) != 2 or {arm.get("arm") for arm in arms if isinstance(arm, dict)} != {"baseline", "candidate"}:
        fail(errors, "arm records are incomplete")
        arms = []
    fingerprints = set()
    for arm in arms:
        if not isinstance(arm, dict):
            fail(errors, "arm record is not an object")
            continue
        name = arm.get("arm")
        expected = BASELINE_PAYLOAD_SHA256 if name == "baseline" else CANDIDATE_PAYLOAD_SHA256 if name == "candidate" else None
        base_version = "1.15.6" if name == "baseline" else "1.15.9"
        expected_identity = {
            "version": BASELINE_VERSION if name == "baseline" else CANDIDATE_VERSION,
            "baseline_commit": BASELINE_COMMIT if name == "baseline" else None,
            "source_commit": BASELINE_COMMIT if name == "baseline" else candidate_source_commit,
            "payload_sha256": expected,
        }
        workdir_value = arm.get("workdir")
        workdir = Path(workdir_value) if isinstance(workdir_value, str) else Path("relative-invalid-workdir")
        expected_workdir = record_path.parent / "arms" / str(name)
        if workdir != expected_workdir or arm.get("workdir_sha256") != sha256(str(expected_workdir).encode()):
            fail(errors, f"{name} exact arm-root binding mismatch")
        identity = object_or_empty(arm.get("identity"), f"{name}.identity", errors)
        fresh_store = object_or_empty(arm.get("fresh_store"), f"{name}.fresh_store", errors)
        if arm.get("live_launcher_mechanism") != "phase2-common-live-harness-v1":
            fail(errors, f"{name} common live-launcher binding mismatch")
        transport = object_or_empty(arm.get("transport_files"), f"{name}.transport_files", errors)
        for transport_name, relative, expected_mode in (("launcher", "scripts/cortex-dev", "0600"), ("observer", "scripts/cortex-desktop-dev", "0700")):
            identity_record = object_or_empty(transport.get(transport_name), f"{name}.transport_files.{transport_name}", errors)
            path = workdir / relative
            try:
                info = path.lstat()
                valid_file = (identity_record.get("relative_path") == relative
                              and identity_record.get("mode") == expected_mode
                              and hex64(identity_record.get("sha256"))
                              and not path.is_symlink() and stat.S_ISREG(info.st_mode)
                              and info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == int(expected_mode, 8)
                              and file_sha256(path) == identity_record.get("sha256"))
            except OSError:
                valid_file = False
            if not valid_file:
                fail(errors, f"{name} archived {transport_name} identity mismatch")
        if real_private_dir(workdir, errors) and expected:
            try:
                for parent, directories, files in os.walk(workdir, followlinks=False):
                    if any((Path(parent) / item).is_symlink() for item in (*directories, *files)):
                        fail(errors, f"{name} workdir contains a symlink")
                plugin = workdir / "plugins" / "cortex"
                actual = payload_digest(plugin, base_version)
                actual_version = payload_version(plugin)
                actual_identity = identity
                if actual_version != expected_identity["version"]:
                    fail(errors, f"{name} archived payload version mismatch")
                if actual != expected or actual_identity != expected_identity or arm.get("workdir_sha256") != sha256(str(workdir).encode()):
                    fail(errors, f"{name} payload digest mismatch")
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                fail(errors, f"{name} payload unreadable: {exc}")
        store = workdir / ".codex" / "cortex" / "cortex.sqlite3"
        if store.exists() or store.is_symlink() or fresh_store != {"status": "observed_absent", "relative_path": ".codex/cortex/cortex.sqlite3", "sha256": None}:
            fail(errors, f"{name} fresh store is not proven absent")
        reset = object_or_empty(arm.get("reset"), f"{name}.reset", errors)
        if reset.get("status") != "contract_sealed" or reset.get("manifest_sha256") != task.get("manifest_sha256"):
            fail(errors, f"{name} reset contract missing")
        for receipt_name, schema in (("terminal_receipt", "phase2-terminal-v1"), ("audit_receipt", "phase2-audit-v1")):
            receipt = object_or_empty(arm.get(receipt_name), f"{name}.{receipt_name}", errors)
            if receipt.get("schema") != schema or receipt.get("status") != "not_started" or receipt.get("exit_code") is not None or receipt.get("open_receipt") is not False or receipt.get("duplicate_receipt") is not False or receipt.get("normalization") != "normalized-preparation-v1":
                fail(errors, f"{name} {receipt_name} is not normalized")
        fingerprints.add(arm.get("control_neutral_fingerprint"))
    if fingerprints != {neutral_fingerprint(neutral)}:
        fail(errors, "control-neutral fingerprints are not symmetric")
    if record.get("live_activity") != {"status": "none", "codex_invoked": False, "listener_touched": False, "network_used": False, "score_or_reveal": False}:
        fail(errors, "live activity is not explicitly absent")
    return {"suite": SUITE, "status": "pass" if not errors else "fail-closed", "errors": errors, "checks": {"fixture_tree_access": "not performed", "live_activity": "none", "quality_score": "not produced"}}


def audit(record_path: Path) -> dict[str, Any]:
    """Never let malformed nested evidence escape as a traceback."""
    try:
        return _audit_impl(record_path)
    except Exception as exc:  # defensive boundary for hostile/tampered JSON shapes
        return {
            "suite": SUITE,
            "status": "fail-closed",
            "errors": [f"malformed control record: {type(exc).__name__}"],
            "checks": {"fixture_tree_access": "not performed", "live_activity": "unknown", "quality_score": "not produced"},
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("record")
    args = parser.parse_args(argv)
    result = audit(Path(args.record).resolve())
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
