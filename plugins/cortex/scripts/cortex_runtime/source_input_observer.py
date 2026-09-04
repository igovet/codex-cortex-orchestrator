"""Observe selected root input into the private inbox, never authorize it.

UserPromptSubmit supplies text/session/turn but does not independently prove
human authorship or transport redelivery identity. Preserve each observed event;
do not deduplicate equal text or treat capture as permission to change a task.
"""
from __future__ import annotations

from pathlib import Path
from collections.abc import Mapping

from cortex_runtime.audience_attestation import _key
from cortex_runtime.submission_queue import capture
from cortex_runtime.v12_store import V12Store


def capture_prompt(event: Mapping, *, selected: bool, child: bool, plugin_data: Path) -> str:
    """Return a bounded observation state; never return prompt/locator/error text."""
    if selected is not True or child is not False or event.get("hook_event_name") != "UserPromptSubmit":
        return "ignored"
    if event.get("agent_id"):
        return "ignored"
    session, turn, prompt, cwd = (event.get(key) for key in ("session_id", "turn_id", "prompt", "cwd"))
    if any(not isinstance(value, str) or not value for value in (session, turn, prompt, cwd)):
        return "unavailable"
    try:
        encoded_size = len(prompt.encode("utf-8"))
    except UnicodeError:
        return "unavailable"
    if len(session) > 512 or len(turn) > 512 or not prompt.strip() or encoded_size > 2 * 1024 * 1024:
        return "unavailable"
    try:
        project = Path(cwd)
        if not project.is_absolute() or project.resolve(strict=True) != project or not project.is_dir():
            return "unavailable"
        if project == Path(project.anchor):
            return "unavailable"
        if not plugin_data.is_absolute() or any(path.is_symlink() for path in (plugin_data / "activation", plugin_data, *plugin_data.parents)):
            return "unavailable"
        key = _key(plugin_data, create=True)
        store = V12Store(project)
        store._write(lambda connection: capture(connection, session=session, turn=turn, text=prompt, key=key))
        return "captured_unverified_origin"
    except (OSError, ValueError, TypeError, RuntimeError):
        # Missing capture never fabricates a source receipt or changes user input.
        # The future public bootstrap must require its own verified source binding.
        return "unavailable"
