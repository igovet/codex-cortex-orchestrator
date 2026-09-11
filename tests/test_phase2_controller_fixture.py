import json
import subprocess
from pathlib import Path

from fixtures.phase2_controller.controller_fixture import (
    OfflineController,
    ROW_ID,
    audit_receipts,
    build_controller_artifact,
    build_git_ready_repo,
    canonical_cell_path,
    open_sealed_cell,
)


def test_git_ready_repo_has_deterministic_log_and_unchanged_content_tree(tmp_path):
    first = build_git_ready_repo(tmp_path / "first")
    second = build_git_ready_repo(tmp_path / "second")

    assert first["receipt"] == {"operation": "git_log", "outcome": "success", "exit_code": 0, "reason": None}
    assert first["commit"] == second["commit"]
    assert first["before"] == first["after"] == second["before"] == second["after"]
    assert subprocess.run(
        ["git", "log", "-1", "--format=%H"], cwd=tmp_path / "first", check=False,
        capture_output=True, text=True,
    ).returncode == 0
    assert (tmp_path / "first" / "USER-NOTE.txt").read_bytes() == b"Protected starting content.\n"


def test_sealed_row_is_single_source_and_mismatch_is_prelaunch_failure(tmp_path):
    cells = tmp_path / "cells"
    canonical = canonical_cell_path(cells, ROW_ID)
    opened = open_sealed_cell(cells, ROW_ID)
    assert opened["canonical_path"] == opened["opened_path"] == canonical.as_posix()
    assert opened["row_id"] == Path(opened["opened_path"]).parent.name == ROW_ID
    mismatch = open_sealed_cell(cells, ROW_ID, cells / "P2-01-7c7a3c5b6e7f8a9b" / "project")
    assert mismatch["receipt"]["exit_code"] == 1
    assert mismatch["receipt"]["reason"] == "sealed_path_mismatch"
    assert mismatch["launched"] is False
    assert not (cells / "P2-01-7c7a3c5b6e7f8a9b").exists()


def test_pending_wait_fails_audit_but_records_stop_and_terminal_wait_accepts(tmp_path):
    project = tmp_path / "cells" / ROW_ID / "project"
    project.mkdir(parents=True)
    pending = OfflineController(project).audit_and_stop()
    assert pending["audit"]["exit_code"] == 1
    assert pending["audit"]["failures"] == ["pending_wait_agent"]
    assert pending["stop"]["exit_code"] == 0
    assert pending["accepted"] is False

    controller = OfflineController(project)
    controller.resolve_wait("success")
    resolved = controller.audit_and_stop()
    assert resolved["wait"]["outcome"] == "success"
    assert resolved["audit"]["exit_code"] == resolved["stop"]["exit_code"] == 0
    assert resolved["accepted"] is True


def test_audit_wrapper_fails_closed_for_bad_receipts(tmp_path):
    project = tmp_path / "cells" / ROW_ID / "project"
    project.mkdir(parents=True)
    valid = {"operation": "open_cell", "outcome": "success", "exit_code": 0,
             "opened_path": project.as_posix()}
    terminal_wait = {"operation": "wait_agent", "outcome": "success", "exit_code": 0}
    assert audit_receipts(
        [valid, terminal_wait], row_id=ROW_ID, project_path=project.as_posix()
    )["exit_code"] == 0
    assert audit_receipts([], row_id=ROW_ID, project_path=project.as_posix())["exit_code"] == 1
    cases = [
        {"operation": "git_log", "outcome": "error", "exit_code": 128},
        {"operation": "wait_agent", "outcome": "pending", "exit_code": 1},
        {"operation": "open_cell", "outcome": "success", "exit_code": 0,
         "opened_path": (project.parent / "other" / "project").as_posix()},
        {"operation": "unknown"},
    ]
    for bad in cases:
        result = audit_receipts([bad], row_id=ROW_ID, project_path=project.as_posix())
        assert result["exit_code"] == 1
        assert result["outcome"] == "error"
        assert result["failures"]


def test_audit_requires_exactly_one_terminal_wait_receipt(tmp_path):
    project = tmp_path / "cells" / ROW_ID / "project"
    project.mkdir(parents=True)
    open_cell = {
        "operation": "open_cell",
        "outcome": "success",
        "exit_code": 0,
        "opened_path": project.as_posix(),
    }

    omitted = audit_receipts([open_cell], row_id=ROW_ID, project_path=project.as_posix())
    assert omitted["exit_code"] != 0
    assert "missing_wait_agent" in omitted["failures"]

    pending = audit_receipts(
        [open_cell, {"operation": "wait_agent", "outcome": "pending", "exit_code": 1}],
        row_id=ROW_ID,
        project_path=project.as_posix(),
    )
    assert pending["exit_code"] != 0
    assert {"pending_wait_agent", "nonterminal_wait_agent"}.issubset(pending["failures"])

    duplicate = audit_receipts(
        [
            open_cell,
            {"operation": "wait_agent", "outcome": "success", "exit_code": 0},
            {"operation": "wait_agent", "outcome": "stopped", "exit_code": 0},
        ],
        row_id=ROW_ID,
        project_path=project.as_posix(),
    )
    assert duplicate["exit_code"] != 0
    assert "duplicate_wait_agent" in duplicate["failures"]

    terminal = audit_receipts(
        [open_cell, {"operation": "wait_agent", "outcome": "stopped", "exit_code": 0}],
        row_id=ROW_ID,
        project_path=project.as_posix(),
    )
    assert terminal["exit_code"] == 0
    assert terminal["outcome"] == "success"


def test_preserved_artifact_contains_private_receipts_and_content_snapshots(tmp_path):
    output = tmp_path / "artifact"
    manifest = build_controller_artifact(output)
    assert manifest["git_log"]["exit_code"] == 0
    assert manifest["path_match"]["exit_code"] == 0
    assert manifest["path_mismatch"]["exit_code"] == 1
    assert manifest["pending_audit_exit"] == 1
    assert manifest["pending_stop_exit"] == 0
    assert manifest["terminal_audit_exit"] == manifest["terminal_stop_exit"] == 0
    assert manifest["content_tree_equal"] is True
    assert output.stat().st_mode & 0o777 == 0o700
    for path in output.rglob("*.json"):
        assert path.stat().st_mode & 0o777 == 0o600
    before = json.loads((output / "snapshots" / "content-before.json").read_text())
    after = json.loads((output / "snapshots" / "content-after.json").read_text())
    assert before == after
    assert {row["path"] for row in before["content_tree"]} == {"README.md", "USER-NOTE.txt"}


def test_fixture_cli_refuses_existing_output(tmp_path):
    output = tmp_path / "artifact"
    command = ["python3", "tests/fixtures/phase2_controller/controller_fixture.py", "--output", str(output)]
    first = subprocess.run(command, check=False, capture_output=True, text=True)
    second = subprocess.run(command, check=False, capture_output=True, text=True)
    assert first.returncode == 0, first.stderr
    assert second.returncode != 0
