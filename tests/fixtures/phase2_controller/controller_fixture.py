"""Offline controller contract fixture for the Phase 2 repair.

This module deliberately has no Cortex/runtime imports.  It models only the
fixture and controller boundaries that failed in the held-out run:

* a disposable repository has one deterministic baseline commit;
* a sealed row ID is the sole source for its cell directory;
* audit/stop records a terminal wait before accepting a cell; and
* any malformed or failed receipt makes the audit fail closed.

The command-line entry point writes private evidence (receipts and content-tree
snapshots) to an explicitly supplied output directory.  Tests use a temporary
directory, while reviewers can preserve a run by choosing a disposable path.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


ROW_ID = "P2-01-45168c561572dcf3"
_ROW_PATTERN = re.compile(r"^P2-[0-9]{2}-[0-9a-f]{16}$")
_FIXTURE_FILES = {
    "README.md": b"# Offline Phase 2 fixture\n",
    "USER-NOTE.txt": b"Protected starting content.\n",
}
_FIXED_DATE = "2000-01-01T00:00:00Z"
_GIT_ENV = {
    "GIT_AUTHOR_NAME": "Cortex Offline Fixture",
    "GIT_AUTHOR_EMAIL": "cortex-offline-fixture@example.invalid",
    "GIT_COMMITTER_NAME": "Cortex Offline Fixture",
    "GIT_COMMITTER_EMAIL": "cortex-offline-fixture@example.invalid",
    "GIT_AUTHOR_DATE": _FIXED_DATE,
    "GIT_COMMITTER_DATE": _FIXED_DATE,
}


class ControllerFailure(RuntimeError):
    """A deliberately observable fail-closed controller outcome."""

    exit_code = 1


@dataclass(frozen=True)
class Receipt:
    operation: str
    outcome: str
    exit_code: int
    reason: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "operation": self.operation,
            "outcome": self.outcome,
            "exit_code": self.exit_code,
            "reason": self.reason,
        }


def _private_directory(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=False)
    os.chmod(path, 0o700)


def _private_file(path: Path, value: dict[str, object]) -> None:
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.chmod(path, 0o600)


def _run_git(project: Path, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    merged = os.environ.copy()
    if env:
        merged.update(env)
    return subprocess.run(
        ["git", *args],
        cwd=project,
        env=merged,
        text=True,
        capture_output=True,
        check=False,
    )


def content_tree(project: Path) -> list[dict[str, object]]:
    """Return a deterministic snapshot of fixture content, excluding .git."""
    rows: list[dict[str, object]] = []
    for path in sorted(project.rglob("*")):
        if not path.is_file() or ".git" in path.parts:
            continue
        relative = path.relative_to(project).as_posix()
        payload = path.read_bytes()
        rows.append({
            "path": relative,
            "size": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        })
    return rows


def _write_snapshot(root: Path, name: str, rows: Iterable[dict[str, object]]) -> None:
    _private_file(root / name, {"content_tree": list(rows)})


def build_git_ready_repo(project: Path) -> dict[str, object]:
    """Create a deterministic disposable repository and prove ``git log``."""
    _private_directory(project)
    for name, payload in _FIXTURE_FILES.items():
        (project / name).write_bytes(payload)

    before = content_tree(project)
    init = _run_git(project, "init", "-q", "-b", "main")
    if init.returncode != 0:
        raise ControllerFailure(f"git init failed: {init.stderr.strip()}")
    add = _run_git(project, "add", "--", *sorted(_FIXTURE_FILES))
    if add.returncode != 0:
        raise ControllerFailure(f"git add failed: {add.stderr.strip()}")
    commit = _run_git(
        project,
        "-c", "commit.gpgSign=false",
        "commit", "--no-gpg-sign", "--date", _FIXED_DATE,
        "--author", "Cortex Offline Fixture <cortex-offline-fixture@example.invalid>",
        "-m", "fixture baseline",
        env=_GIT_ENV,
    )
    if commit.returncode != 0:
        raise ControllerFailure(f"git commit failed: {commit.stderr.strip()}")
    log = _run_git(project, "log", "-1", "--format=%H")
    receipt = Receipt("git_log", "success" if log.returncode == 0 else "error", log.returncode)
    if log.returncode != 0 or not re.fullmatch(r"[0-9a-f]{40}\n?", log.stdout):
        raise ControllerFailure("git log did not return one commit")
    after = content_tree(project)
    if before != after:
        raise ControllerFailure("baseline commit changed fixture content")
    return {
        "receipt": receipt.as_dict(),
        "commit": log.stdout.strip(),
        "before": before,
        "after": after,
    }


def canonical_cell_path(cells_root: Path, row_id: str) -> Path:
    """Derive the one permitted project path from the sealed row ID."""
    if not isinstance(row_id, str) or not _ROW_PATTERN.fullmatch(row_id):
        raise ControllerFailure("sealed row ID is invalid")
    return cells_root / row_id / "project"


def open_sealed_cell(cells_root: Path, row_id: str, observed_path: Path | None = None) -> dict[str, object]:
    """Open only the path derived from the sealed ID, before launch/input."""
    canonical = canonical_cell_path(cells_root, row_id)
    if observed_path is not None and observed_path != canonical:
        return {
            "row_id": row_id,
            "canonical_path": canonical.as_posix(),
            "opened_path": observed_path.as_posix(),
            "receipt": Receipt("open_cell", "error", 1, "sealed_path_mismatch").as_dict(),
            "launched": False,
        }
    canonical.mkdir(mode=0o700, parents=True, exist_ok=False)
    os.chmod(canonical, 0o700)
    return {
        "row_id": row_id,
        "canonical_path": canonical.as_posix(),
        "opened_path": canonical.as_posix(),
        "receipt": Receipt("open_cell", "success", 0).as_dict(),
        "launched": False,
    }


def audit_receipts(
    receipts: Iterable[dict[str, object]],
    *,
    row_id: str,
    project_path: str,
) -> dict[str, object]:
    """Audit all receipts fail-closed, including malformed records."""
    failures: list[str] = []
    receipt_rows = list(receipts)
    try:
        project = Path(project_path)
        if not project.is_absolute() or project.name != "project" or project.parent.name != row_id:
            raise ControllerFailure("project path is not canonical")
        expected_path = canonical_cell_path(project.parents[1], row_id).as_posix()
        if project.as_posix() != expected_path:
            raise ControllerFailure("project path is not canonical")
    except (ControllerFailure, IndexError, TypeError, ValueError):
        expected_path = None
        failures.append("sealed_path_mismatch")
    if not receipt_rows:
        failures.append("no_receipts")
    wait_receipts = [
        receipt
        for receipt in receipt_rows
        if isinstance(receipt, dict) and receipt.get("operation") == "wait_agent"
    ]
    if not wait_receipts:
        failures.append("missing_wait_agent")
    elif len(wait_receipts) != 1:
        failures.append("duplicate_wait_agent")
    elif not (
        wait_receipts[0].get("outcome") in {"success", "stopped", "cancelled"}
        and wait_receipts[0].get("exit_code") == 0
    ):
        failures.append("nonterminal_wait_agent")
    allowed_operations = {"git_log", "open_cell", "wait_agent", "stop"}
    for receipt in receipt_rows:
        if not isinstance(receipt, dict):
            failures.append("malformed_receipt")
            continue
        operation = receipt.get("operation")
        outcome = receipt.get("outcome")
        code = receipt.get("exit_code")
        if not isinstance(operation, str) or not isinstance(outcome, str) or not isinstance(code, int):
            failures.append("malformed_receipt")
            continue
        if operation not in allowed_operations:
            failures.append("unknown_operation")
        if code != 0 or outcome not in {"success", "stopped", "cancelled"}:
            failures.append(f"{operation}:{outcome}")
        if operation == "open_cell":
            if expected_path is None or receipt.get("opened_path") != expected_path:
                failures.append("sealed_path_mismatch")
        if operation == "wait_agent" and outcome == "pending":
            failures.append("pending_wait_agent")
    return {
        "operation": "audit",
        "outcome": "success" if not failures else "error",
        "exit_code": 0 if not failures else 1,
        "failures": sorted(set(failures)),
    }


class OfflineController:
    """Minimal wait/audit/stop state machine used only by this fixture."""

    def __init__(self, project_path: Path, row_id: str = ROW_ID):
        self.project_path = project_path
        self.row_id = row_id
        self.wait = Receipt("wait_agent", "pending", 1, "not_terminal")

    def resolve_wait(self, outcome: str = "success") -> Receipt:
        if outcome not in {"success", "stopped", "error", "cancelled"}:
            raise ValueError("wait outcome must be terminal")
        code = 0 if outcome in {"success", "stopped", "cancelled"} else 1
        self.wait = Receipt("wait_agent", outcome, code)
        return self.wait

    def audit_and_stop(self) -> dict[str, object]:
        wait_receipt = self.wait.as_dict()
        if self.wait.outcome == "pending":
            audit = {
                "operation": "audit",
                "outcome": "error",
                "exit_code": 1,
                "failures": ["pending_wait_agent"],
            }
            stop = Receipt("stop", "stopped", 0, "audit_failed_pending_wait").as_dict()
            return {"wait": wait_receipt, "audit": audit, "stop": stop, "accepted": False}
        audit = audit_receipts(
            [wait_receipt], row_id=self.row_id, project_path=self.project_path.as_posix()
        )
        stop = Receipt("stop", "stopped", 0).as_dict()
        return {"wait": wait_receipt, "audit": audit, "stop": stop, "accepted": audit["exit_code"] == 0}


def build_controller_artifact(output: Path) -> dict[str, object]:
    """Build positive and negative receipts plus content-tree snapshots."""
    _private_directory(output)
    snapshots = output / "snapshots"
    receipts = output / "receipts"
    _private_directory(snapshots)
    _private_directory(receipts)
    project = output / "cells" / ROW_ID / "project"
    git_result = build_git_ready_repo(project)
    _write_snapshot(snapshots, "content-before.json", git_result["before"])
    _write_snapshot(snapshots, "content-after.json", git_result["after"])
    _private_file(receipts / "git-log-positive.json", git_result["receipt"])

    opened = {
        "row_id": ROW_ID,
        "canonical_path": project.as_posix(),
        "opened_path": project.as_posix(),
        "receipt": Receipt("open_cell", "success", 0).as_dict(),
        "launched": False,
    }
    mismatch = open_sealed_cell(output / "cells", ROW_ID, output / "cells" / "P2-01-7c7a3c5b6e7f8a9b" / "project")
    _private_file(receipts / "sealed-path-positive.json", opened)
    _private_file(receipts / "sealed-path-negative.json", mismatch)

    pending = OfflineController(project).audit_and_stop()
    _private_file(receipts / "pending-audit-negative.json", pending)
    terminal = OfflineController(project)
    terminal.resolve_wait("success")
    resolved = terminal.audit_and_stop()
    _private_file(receipts / "terminal-audit-positive.json", resolved)

    manifest = {
        "fixture": "phase2-controller-v1",
        "row_id": ROW_ID,
        "project": f"cells/{ROW_ID}/project",
        "git_log": git_result["receipt"],
        "path_match": opened["receipt"],
        "path_mismatch": mismatch["receipt"],
        "pending_audit_exit": pending["audit"]["exit_code"],
        "pending_stop_exit": pending["stop"]["exit_code"],
        "terminal_audit_exit": resolved["audit"]["exit_code"],
        "terminal_stop_exit": resolved["stop"]["exit_code"],
        "content_tree_equal": git_result["before"] == git_result["after"],
    }
    _private_file(output / "manifest.json", manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="new private evidence directory")
    args = parser.parse_args()
    build_controller_artifact(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
