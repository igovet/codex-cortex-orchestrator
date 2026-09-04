"""Recovery ordering over real source MCP stdio, not a mocked handler."""
from test_command_receipts import _source_stdio_session


def data(reply):
    assert not reply["result"].get("isError"), reply
    return reply["result"]["structuredContent"]


def error(reply, code):
    assert reply["result"]["isError"]
    assert reply["result"]["structuredContent"]["error"]["code"] == code


def test_fresh_recovery_requires_state_then_nonempty_continuations(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    with _source_stdio_session(str(home)) as original:
        task = data(original("open_task", {
            "project_root": str(project), "request_original": "Inspect the fixture.",
            "user_language": "en", "constraints": ["Read-only inspection."],
            "outcomes": [{"outcome": "Inspect fixture", "acceptance": [], "constraints": [], "verification": []}],
        }))["task_ref"]
        data(original("assess_governance", {"task_ref": task, "mode": "minimal"}))
        data(original("read_scope", {"task_ref": task, "responsibility": "evidence"}))
        data(original("open_assignment", {
            "task_ref": task, "profile_name": "explorer",
            "model": "gpt-5.6-luna", "reasoning_effort": "high", "nodes": ["baseline"],
        }))
        ordinary = data(original("read_state", {"task_ref": task}))
        assert ordinary["data"]["unfinished_assignment_count"] == 1
        assert ordinary["data"]["recovery_required"] is False
        data(original("read_scope", {"task_ref": task, "responsibility": "evidence"}))
    with _source_stdio_session(str(home)) as recovered:
        error(recovered("read_scope", {"task_ref": task, "responsibility": "evidence"}), "recovery_state_required")
        state = data(recovered("read_state", {"task_ref": task}))
        assert state["data"]["admissible_operations"] == ["read_continuations"]
        for tool, args in (
            ("read_scope", {"responsibility": "evidence"}),
            ("read_timeline", {}), ("read_state", {}),
            ("assess_governance", {"mode": "minimal"}),
        ):
            error(recovered(tool, {"task_ref": task, **args}), "recovery_continuations_required")
        view = data(recovered("read_continuations", {"task_ref": task}))
        assert view["has_more"] is False
        assert len(view["data"]["continuations"]) == 1
        data(recovered("read_scope", {"task_ref": task, "responsibility": "evidence"}))


def test_recovery_without_unfinished_work_does_not_require_continuations(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    with _source_stdio_session(str(home)) as first:
        task = data(first("open_task", {
            "project_root": str(tmp_path), "request_original": "Inspect fixture.",
            "user_language": "en", "constraints": ["Read-only."],
            "outcomes": [{"outcome": "Inspect fixture", "acceptance": [], "constraints": [], "verification": []}],
        }))["task_ref"]
    with _source_stdio_session(str(home)) as recovered:
        state = data(recovered("read_state", {"task_ref": task}))
        assert state["data"]["unfinished_assignment_count"] == 0
        assert state["data"]["recovery_required"] is False
        data(recovered("read_scope", {"task_ref": task, "responsibility": "planning"}))
