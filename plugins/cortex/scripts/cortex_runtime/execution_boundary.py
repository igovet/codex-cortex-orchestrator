"""Deny-only pre-dispatch protection for a task's private Cortex store.

This module deliberately knows nothing about reports, assignments, routing or
acceptance.  It only classifies tool operands against the confirmed project
root.  The bounded ``mcp__cortex__read_report`` operation is not passed here;
that MCP route remains the worker's approved evidence boundary.
"""
from __future__ import annotations

import re
import shlex
from pathlib import Path


PRIVATE_ROOT_PARTS = (".codex", "cortex")
SHELL_TOOLS = frozenset({"Bash", "exec_command", "write_stdin", "terminal"})
FILE_TOOLS = frozenset({"read_file", "write_file"})
BOUNDARY_TOOLS = SHELL_TOOLS | FILE_TOOLS | frozenset({"apply_patch"})
PRIVATE_MARKER = re.compile(r"(?<![A-Za-z0-9_.-])\.codex[\\/]cortex(?=$|[\\/\\s\"'`;|&<>()])")
PATH_FIELDS = frozenset({"path", "file_path", "filename", "target_file", "file",
                         "cwd", "workdir", "working_directory", "directory"})
COMMAND_FIELDS = frozenset({"cmd", "command", "chars"})
CONTROL = frozenset({";", "&&", "||", "|", "<", ">", ">>", "<<", "&", "(", ")"})


def _private_root(project_root: str) -> Path:
    return Path(project_root).resolve() / Path(*PRIVATE_ROOT_PARTS)


def _under_private(value: str, *, cwd: str, project_root: str) -> bool:
    """Check lexical and resolved forms without opening or retaining a file."""
    if not isinstance(value, str) or not value or "\x00" in value:
        return False
    root = _private_root(project_root)
    candidate = Path(value).expanduser() if value.startswith("~") else Path(value)
    if not candidate.is_absolute():
        candidate = Path(cwd) / candidate
    try:
        # ``resolve`` uses metadata only.  It catches existing symlink routes,
        # including links whose visible path has no Cortex marker.
        resolved = candidate.resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        return bool(PRIVATE_MARKER.search(value.replace("\\", "/")))
    return resolved == root or root in resolved.parents


def _tokens(command: str):
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        return list(lexer)
    except (ValueError, TypeError):
        return None


def _static_search_pattern(tokens: list[str], marker: str, *, cwd: str, project_root: str) -> bool:
    """Allow only a quoted/static rg or grep search marker, never a path leaf."""
    if not tokens or tokens[0] not in {"rg", "grep"}:
        return False
    positional: list[str] = []
    after_double_dash = False
    marker_is_pattern = False
    pattern_options = {"-e", "--regexp", "-g", "--glob", "--iglob", "--include", "--exclude", "--exclude-dir"}
    i = 1
    while i < len(tokens):
        token = tokens[i]
        if token == "--":
            after_double_dash = True
            i += 1
            continue
        if not after_double_dash and token in pattern_options:
            if i + 1 >= len(tokens):
                return False
            marker_is_pattern |= marker in tokens[i + 1]
            i += 2
            continue
        if not after_double_dash and token.startswith("-"):
            i += 1
            continue
        positional.append(token)
        i += 1
    # An implicit first positional is the pattern; a later one is a path.
    path_operands = positional if marker_is_pattern else positional[1:]
    return bool((marker_is_pattern or positional and marker in positional[0]) and
                not any(_under_private(item, cwd=cwd, project_root=project_root)
                        for item in path_operands))


def _command_hits_private(command: str, *, cwd: str, project_root: str) -> bool:
    normalized = command.replace("\\", "/")
    if not PRIVATE_MARKER.search(normalized):
        # A symlink can hide the root marker, so inspect simple path operands
        # even when the command text has no literal marker.
        tokens = _tokens(command)
        if not tokens:
            return False
        for token in tokens:
            if token in CONTROL or token.startswith("-"):
                continue
            # Resolve every simple token, including a bare symlink name. A
            # failed/nonexistent candidate is harmless and never read.
            if _under_private(token, cwd=cwd, project_root=project_root):
                return True
        return False

    tokens = _tokens(command)
    # Unparseable or shell-ambiguous marker-bearing commands fail closed. This
    # covers expansion, cd-then-read, redirection, pipelines and substitutions.
    if not tokens or any(token in CONTROL for token in tokens):
        return True
    if any(any(char in token for char in "$`*?[]{}~") for token in tokens):
        return True
    if tokens[0] in {"echo", "printf"}:
        return False
    if _static_search_pattern(tokens, ".codex/cortex", cwd=cwd, project_root=project_root):
        # A marker used as the search pattern is harmless when all path
        # operands remain outside the private root.
        return False
    # Any remaining marker-bearing command is a direct reader/probe or opaque
    # code. Deny before dispatch rather than trying to evaluate the shell.
    return True


def _patch_paths(command: str):
    lines = command.strip().splitlines()
    if len(lines) < 2 or lines[0] != "*** Begin Patch" or lines[-1] != "*** End Patch":
        return None
    paths = []
    for line in lines:
        match = re.fullmatch(r"\*\*\* (?:Add|Delete|Update) File: (.+)", line)
        if match:
            paths.append(match[1])
        match = re.fullmatch(r"\*\*\* Move to: (.+)", line)
        if match:
            paths.append(match[1])
    return paths


def _file_input_hits_private(tool_input, *, cwd: str, project_root: str) -> bool:
    if not isinstance(tool_input, dict):
        return False
    for key, value in tool_input.items():
        if key in PATH_FIELDS and isinstance(value, str) and _under_private(value, cwd=cwd, project_root=project_root):
            return True
    return False


def private_access_denied(tool: str, tool_input, *, cwd: str, project_root: str) -> bool:
    """Return whether a pre-dispatch operand reaches task-private Cortex data."""
    if tool not in BOUNDARY_TOOLS:
        return False
    if tool in FILE_TOOLS:
        return _file_input_hits_private(tool_input, cwd=cwd, project_root=project_root)
    if tool == "apply_patch":
        command = tool_input.get("command") if isinstance(tool_input, dict) else None
        paths = _patch_paths(command) if isinstance(command, str) else None
        return bool(paths and any(_under_private(path, cwd=cwd, project_root=project_root) for path in paths))
    if not isinstance(tool_input, dict):
        return False
    for key, value in tool_input.items():
        if key in COMMAND_FIELDS and isinstance(value, str) and _command_hits_private(value, cwd=cwd, project_root=project_root):
            return True
    return False
