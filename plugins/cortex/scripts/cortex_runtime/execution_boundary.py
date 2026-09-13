"""Small, fail-closed pre-dispatch authorization for observable host tools.

The existing private-store operand classifier remains deliberately narrow.  The
semantic authorizer composes with it for the host tool families that lifecycle
hooks already observe; it neither changes Cortex's public MCP schemas nor owns
assignment, pipeline, or acceptance decisions.
"""
from __future__ import annotations

import hashlib
import json
import re
import shlex
from pathlib import Path


PRIVATE_ROOT_PARTS = (".codex", "cortex")
SHELL_TOOLS = frozenset({"Bash", "exec_command", "write_stdin", "terminal"})
FILE_TOOLS = frozenset({"read_file", "write_file"})
BOUNDARY_TOOLS = SHELL_TOOLS | FILE_TOOLS | frozenset({"apply_patch"})
PERMISSION_DENIED = "PERMISSION_DENIED"
CAPABILITY_NOT_GRANTED = "CAPABILITY_NOT_GRANTED"
CAPABILITY_EXECUTE = "cortex.runtime.execute"
CAPABILITY_OBSERVE = "cortex.runtime.observe"
CAPABILITY_TASK_WRITE = "cortex.task.write"
CAPABILITY_INTERNAL = "cortex.internal.*"
CAPABILITY_HOST_DISPATCH = "cortex.host.dispatch"
HOST_DISPATCH_TOOL = "functions.exec"
HOST_DISPATCH_ROUTE = "native_functions_exec_pre_dispatch"
HOST_DISPATCH_PHASES = frozenset({"pre_task", "bound_task"})
HOST_DISPATCH_ACTORS = frozenset({"native_coordinator", "native_worker"})
HOST_DISPATCH_ROLES = frozenset({"coordinator", "worker"})
HOST_DISPATCH_ACTOR_ROLES = {
    "native_coordinator": "coordinator",
    "native_worker": "worker",
}
HOST_DISPATCH_OPERATION_CLASSES = frozenset({
    "cortex_bootstrap", "cortex_mcp", "workspace_execution",
    "workspace_observation", "workspace_mutation",
})
OPERATION_CAPABILITIES = {
    "Bash": CAPABILITY_EXECUTE,
    "exec_command": CAPABILITY_EXECUTE,
    "write_stdin": CAPABILITY_EXECUTE,
    "terminal": CAPABILITY_OBSERVE,
    "read_file": CAPABILITY_OBSERVE,
    "write_file": CAPABILITY_TASK_WRITE,
    "apply_patch": CAPABILITY_TASK_WRITE,
}
WORKER_CAPABILITIES = frozenset({CAPABILITY_EXECUTE, CAPABILITY_OBSERVE, CAPABILITY_TASK_WRITE})
PRIVATE_MARKER = re.compile(r"(?<![A-Za-z0-9_.-])\.codex[\\/]cortex(?=$|[\\/\\s\"'`;|&<>()])")
PRIVATE_HOST_MARKER = re.compile(
    r"(?:[\\/]\.codex[\\/]plugins[\\/]|[\\/]plugins[\\/]cache[\\/]|"
    r"[\\/]\.cortex-dev[\\/]candidates[\\/]|[\\/]\.codex[\\/]agents[\\/]|"
    r"[\\/]\.codex[\\/]cortex(?:$|[\\/]))"
)
PATH_FIELDS = frozenset({"path", "file_path", "filename", "target_file", "file",
                         "cwd", "workdir", "working_directory", "directory"})
COMMAND_FIELDS = frozenset({"cmd", "command", "chars"})
CONTROL = frozenset({";", "&&", "||", "|", "<", ">", ">>", "<<", "&", "(", ")"})
ACTIVE_SKILL_READ_CAPABILITY = "cortex.skill.read"
_SKILL_READ_MAX_LINES = 4000
_SKILL_READ_MAX_BYTES = 256 * 1024
_REGISTERED_WORKER_SKILL_SLUGS = frozenset({
    "accessibility-auditor", "accessibility-fixer", "architect", "backend-dev",
    "build-verification", "code-reviewer", "data-engineer", "database-architect",
    "debugger", "devops-engineer", "explorer", "frontend-dev", "fullstack-dev",
    "general", "mobile-dev", "performance-engineer", "planner", "qa-engineer",
    "refactorer", "security-auditor", "senior-consultant", "technical-writer",
    "ux-designer",
})


def _candidate_skill_root(path: Path):
    """Return one real bundled plugin root for an exact skill leaf.

    This deliberately validates the installed candidate's manifest shape before
    treating its `skills/` subtree as a capability operand.  It is not a cache
    discovery route: callers already supplied the literal leaf and every other
    cache operand remains subject to the normal deny path.
    """
    for parent in (path.parent, *path.parents):
        manifest = parent / ".codex-plugin" / "plugin.json"
        if not manifest.is_file() or manifest.is_symlink():
            continue
        try:
            value = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if (isinstance(value, dict) and value.get("name") == "cortex"
                and value.get("skills") == "./skills/"
                and isinstance(value.get("version"), str)
                and re.fullmatch(r"1\.15\.9\+codex\.sha256\.[0-9a-f]{16}", value["version"])):
            return parent
        return None
    return None


def active_bundled_skill_read(tool: str, tool_input, *, role: str,
                              expected_skill: str | None) -> bool:
    """Recognize one bounded literal read of an assigned bundled skill.

    The host still owns dispatch and file access.  This predicate merely avoids
    classifying the documented, manifest-bound skill leaf itself as a generic
    cache probe.  No directory, glob, alias, second operand, mutation, or
    unassigned worker profile qualifies.
    """
    if tool not in {"exec_command", "Bash"} or role not in {"coordinator", "worker"}:
        return False
    if expected_skill is not None and (not isinstance(expected_skill, str) or not re.fullmatch(
            r"skills/(?:orchestrator|worker-[a-z][a-z-]*)/SKILL\.md", expected_skill)):
        return False
    command = tool_input.get("cmd", tool_input.get("command")) if isinstance(tool_input, dict) else None
    if not isinstance(command, str):
        return False
    # The installed interactive transport may wrap this one literal read in
    # exactly one bash -lc envelope.  Unwrap only that three-token shape; a
    # second shell, option, or command remains outside this exception.
    outer_tokens = _tokens(command)
    if (outer_tokens and len(outer_tokens) == 3
            and outer_tokens[0] in {"bash", "/bin/bash"}
            and outer_tokens[1] == "-lc" and outer_tokens[2]):
        command = outer_tokens[2]
    if not isinstance(command, str) or any(mark in command for mark in ("\n", "$", "`", "*", "?", "[", "]", "{", "}", "|", ";", "&&", "||", ">", "<")):
        return False
    tokens = _tokens(command)
    if not tokens or any(token in CONTROL for token in tokens):
        return False
    # A literal cat of the single skill leaf is bounded by file size. A sed
    # slice is bounded by line count. In both forms the selected full skill is
    # proven only by the observed complete-result marker.
    if len(tokens) == 2 and tokens[0] == "cat":
        path_token = tokens[1]
        max_bytes = _SKILL_READ_MAX_BYTES
    elif len(tokens) == 4 and tokens[0] == "sed" and tokens[1] == "-n":
        match = re.fullmatch(r"1,([1-9][0-9]{0,3})p", tokens[2])
        if match is None or int(match.group(1)) > _SKILL_READ_MAX_LINES:
            return False
        path_token = tokens[3]
        max_bytes = None
    else:
        return False
    supplied = Path(path_token)
    if not supplied.is_absolute() or supplied.is_symlink() or supplied.name != "SKILL.md":
        return False
    try:
        resolved = supplied.resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        return False
    if max_bytes is not None:
        try:
            if resolved.stat().st_size > max_bytes:
                return False
        except OSError:
            return False
    root = _candidate_skill_root(resolved)
    if root is None:
        return False
    try:
        relative = resolved.relative_to(root).as_posix()
    except ValueError:
        return False
    if role == "coordinator":
        return expected_skill == "skills/orchestrator/SKILL.md" and relative == expected_skill
    # The current host can keep the authenticated worker assignment opaque at
    # the first hook event.  Permit only a single registered worker SKILL leaf
    # in that case; the observer must later bind its exact profile and
    # assignment digest to the native result before it becomes usable evidence.
    if expected_skill is None:
        match = re.fullmatch(r"skills/worker-([a-z][a-z-]*)/SKILL\.md", relative)
        return match is not None and match.group(1) in _REGISTERED_WORKER_SKILL_SLUGS
    return relative == expected_skill and expected_skill.startswith("skills/worker-")


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


def internal_host_access_denied(tool: str, tool_input, *, cwd: str, project_root: str) -> bool:
    """Classify known internal/cache operands for the observed host families.

    This is a pre-dispatch capability input, not a general filesystem sandbox.
    It examines only existing bounded command/path fields and retains no operand.
    """
    if private_access_denied(tool, tool_input, cwd=cwd, project_root=project_root):
        return True
    if tool not in BOUNDARY_TOOLS or not isinstance(tool_input, dict):
        return False
    for key, value in tool_input.items():
        if key in PATH_FIELDS | COMMAND_FIELDS and isinstance(value, str):
            if PRIVATE_HOST_MARKER.search(value.replace("\\", "/")):
                return True
    return False


def capabilities_for_role(role: str):
    """Return the fixed P0 host-tool capability set for a verified role.

    These are runtime defaults for already observable host tools, not profile
    claims or transportable tokens. A later receipt-bound capability lifecycle
    may narrow them further without changing this pre-dispatch boundary.
    """
    return WORKER_CAPABILITIES if role == "worker" else frozenset()


def _host_dispatch_denied(reason, capability=CAPABILITY_HOST_DISPATCH):
    """Return the only deny shape a host adapter may expose to a caller."""
    return {"allowed": False, "code": PERMISSION_DENIED,
            "capability": capability, "reason": reason}


def _host_identifier(value):
    return isinstance(value, str) and bool(re.fullmatch(r"[A-Za-z0-9_:/.-]{1,256}", value))


def _host_dispatch_context(envelope):
    """Validate typed host provenance without accepting raw nested operands.

    This is a repository-side adapter contract.  A real host must invoke it
    before evaluating a custom ``functions.exec`` payload; lifecycle hooks do
    not claim that interception capability.
    """
    if not isinstance(envelope, dict) or set(envelope) != {
            "tool", "actor_kind", "actor_authentication", "role", "phase", "task_id", "assignment_id",
            "route", "task_assignment_relation", "nested_calls"}:
        return None
    if envelope["tool"] != HOST_DISPATCH_TOOL:
        return None
    if (envelope["actor_kind"] not in HOST_DISPATCH_ACTORS
            or envelope["actor_authentication"] != "host_authenticated"
            or envelope["role"] not in HOST_DISPATCH_ROLES):
        return None
    # Set membership is insufficient provenance. The host-authenticated native
    # identity and declared semantic role must be the one closed pair before
    # any nested call is parsed or dispatched, in every phase.
    if HOST_DISPATCH_ACTOR_ROLES.get(envelope["actor_kind"]) != envelope["role"]:
        return None
    if envelope["route"] != HOST_DISPATCH_ROUTE or envelope["phase"] not in HOST_DISPATCH_PHASES:
        return None
    phase = envelope["phase"]
    task_id = envelope["task_id"]
    assignment_id = envelope["assignment_id"]
    relation = envelope["task_assignment_relation"]
    if phase == "pre_task":
        if (envelope["actor_kind"], envelope["role"], task_id, assignment_id, relation) != (
                "native_coordinator", "coordinator", None, None, "no_task_yet"):
            return None
    elif (not _host_identifier(task_id) or not _host_identifier(assignment_id)
          or relation != "host_bound_assignment"):
        return None
    calls = envelope["nested_calls"]
    if not isinstance(calls, list) or not calls or len(calls) > 8:
        return None
    return calls


def _host_dispatch_actor_role_mismatch(envelope):
    """Recognize only a complete known cross-pair for a bounded denial reason."""
    if not isinstance(envelope, dict):
        return False
    actor_kind = envelope.get("actor_kind")
    role = envelope.get("role")
    return (actor_kind in HOST_DISPATCH_ACTORS and role in HOST_DISPATCH_ROLES
            and HOST_DISPATCH_ACTOR_ROLES.get(actor_kind) != role)


def _host_nested_call(call):
    """Accept a closed, operand-free operation descriptor and nothing else."""
    if not isinstance(call, dict) or set(call) != {"tool", "operation_class", "target_class", "opaque"}:
        return None
    tool = call["tool"]
    operation_class = call["operation_class"]
    target_class = call["target_class"]
    if (not isinstance(tool, str) or not tool or len(tool) > 128
            or operation_class not in HOST_DISPATCH_OPERATION_CLASSES
            or not isinstance(target_class, str) or not re.fullmatch(r"[a-z_]{1,64}", target_class)
            or call["opaque"] is not False):
        return None
    return tool, operation_class, target_class


def authorize_host_dispatch_envelope(envelope):
    """Fail closed before any nested ``functions.exec`` member can run.

    Pre-task bootstrap is intentionally limited to a single typed Cortex task
    creation operation.  Bound-task workspace operation classes are closed and
    role-scoped; a host adapter must still apply its ordinary operand/path guard
    before execution.  The decision contains no command, path, or raw operand.
    """
    # This check deliberately precedes parsing nested descriptors so a known
    # provenance cross-pair gets a stable, value-free denial and cannot execute.
    if _host_dispatch_actor_role_mismatch(envelope):
        return _host_dispatch_denied("actor_role_mismatch")
    calls = _host_dispatch_context(envelope)
    if calls is None:
        return _host_dispatch_denied("malformed_or_ambiguous_envelope")
    parsed = [_host_nested_call(call) for call in calls]
    if any(call is None for call in parsed):
        return _host_dispatch_denied("opaque_or_unknown_nested_operation")
    phase = envelope["phase"]
    if phase == "pre_task":
        if parsed != [("mcp__cortex__create_task", "cortex_bootstrap", "cortex_api")]:
            return _host_dispatch_denied("pre_task_bootstrap_only")
        return {"allowed": True, "code": None, "capability": CAPABILITY_HOST_DISPATCH,
                "reason": "pre_task_bootstrap"}
    role = envelope["role"]
    for tool, operation_class, target_class in parsed:
        if operation_class == "cortex_bootstrap":
            return _host_dispatch_denied("bootstrap_outside_pre_task")
        if operation_class == "cortex_mcp":
            if not tool.startswith("mcp__cortex__") or target_class != "cortex_api":
                return _host_dispatch_denied("opaque_or_unknown_nested_operation")
            continue
        if role == "coordinator" and operation_class == "workspace_observation" and target_class == "workspace":
            continue
        if role != "worker":
            return _host_dispatch_denied("role_not_granted", CAPABILITY_EXECUTE)
        expected = {
            "workspace_execution": CAPABILITY_EXECUTE,
            "workspace_observation": CAPABILITY_OBSERVE,
            "workspace_mutation": CAPABILITY_TASK_WRITE,
        }.get(operation_class)
        if expected is None or target_class != "workspace":
            return _host_dispatch_denied("opaque_or_unknown_nested_operation")
    return {"allowed": True, "code": None, "capability": CAPABILITY_HOST_DISPATCH,
            "reason": "bound_task_capability_granted"}


def host_dispatch_receipt(envelope, decision):
    """Build a bounded receipt for a host-owned immutable decision stream."""
    calls = _host_dispatch_context(envelope)
    classes = [] if calls is None else [
        call["operation_class"] for call in calls if isinstance(call, dict)
        and isinstance(call.get("operation_class"), str)
    ]
    safe = {
        "tool": envelope.get("tool") if isinstance(envelope, dict) else None,
        "actor_kind": envelope.get("actor_kind") if isinstance(envelope, dict) else None,
        "actor_authentication": envelope.get("actor_authentication") if isinstance(envelope, dict) else None,
        "role": envelope.get("role") if isinstance(envelope, dict) else None,
        "phase": envelope.get("phase") if isinstance(envelope, dict) else None,
        "route": envelope.get("route") if isinstance(envelope, dict) else None,
        "operation_classes": classes,
        "decision": decision.get("code") if isinstance(decision, dict) else PERMISSION_DENIED,
        "capability": decision.get("capability") if isinstance(decision, dict) else CAPABILITY_HOST_DISPATCH,
        "reason": decision.get("reason") if isinstance(decision, dict) else "malformed_or_ambiguous_envelope",
    }
    safe["binding_digest"] = hashlib.sha256(json.dumps({
        "task_id": envelope.get("task_id") if isinstance(envelope, dict) else None,
        "assignment_id": envelope.get("assignment_id") if isinstance(envelope, dict) else None,
        "relation": envelope.get("task_assignment_relation") if isinstance(envelope, dict) else None,
    }, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return safe


def dispatch_host_envelope(envelope, executor):
    """Reference host-adapter atomicity seam used by offline sentinel tests.

    This does not intercept Codex by itself.  It proves the required invocation
    order for a host that adopts the typed contract: no nested member reaches
    ``executor`` unless the whole closed envelope was authorized.
    """
    decision = authorize_host_dispatch_envelope(envelope)
    receipt = host_dispatch_receipt(envelope, decision)
    if not decision["allowed"]:
        return decision, receipt
    return executor(), receipt


def authorize_pre_dispatch(tool: str, tool_input, *, actor_kind: str, role: str | None,
                           task_id: str | None, assignment_id: str | None,
                           route: str | None, capabilities, cwd: str,
                           project_root: str, expected_skill: str | None = None):
    """Return a value-free authorization decision before a protected dispatch.

    Missing actor, role, task, receipt, route, or capability context denies the
    observable host operation. Private Cortex operands are an internal
    capability request and are denied for every role. The caller records only
    this stable decision and capability class; raw operands stay outside the
    receipt and audit stream.
    """
    required = OPERATION_CAPABILITIES.get(tool)
    if required is None:
        return {"allowed": True, "code": None, "capability": None, "reason": None}
    context_complete = (
        actor_kind in {"native_worker", "native_coordinator"}
        and role in {"worker", "coordinator"}
        and isinstance(task_id, str) and bool(task_id)
        and isinstance(assignment_id, str) and bool(assignment_id)
        and route == "native_hook"
        and isinstance(capabilities, frozenset)
    )
    if not context_complete:
        return {"allowed": False, "code": PERMISSION_DENIED,
                "capability": required, "reason": "missing_or_ambiguous_context"}
    if active_bundled_skill_read(tool, tool_input, role=role, expected_skill=expected_skill):
        return {"allowed": True, "code": None, "capability": ACTIVE_SKILL_READ_CAPABILITY,
                "reason": "exact_active_skill_read"}
    if internal_host_access_denied(tool, tool_input, cwd=cwd, project_root=project_root):
        return {"allowed": False, "code": PERMISSION_DENIED,
                "capability": CAPABILITY_INTERNAL, "reason": "internal_operand"}
    if role == "coordinator" and tool == "read_file":
        return {"allowed": True, "code": None, "capability": CAPABILITY_OBSERVE,
                "reason": "coordinator_evidence_read"}
    if role == "coordinator" and tool == "exec_command" and isinstance(tool_input, dict):
        command = tool_input.get('cmd', tool_input.get('command'))
        tokens = _tokens(command) if isinstance(command, str) else None
        # Only literal project evidence reads. Shell operators and scripts do not
        # become read-only merely because the first word looks like a reader.
        if tokens and not any(c in command for c in '\n$`*?[]{}|;&><()'):
            literal_read = (
                len(tokens) == 2 and tokens[0] == 'cat' and not tokens[1].startswith('-')
            ) or (
                len(tokens) == 4 and tokens[:2] == ['sed', '-n']
                and re.fullmatch(r'1,[1-9][0-9]{0,3}p', tokens[2]) is not None
                and not tokens[3].startswith('-')
            )
            if literal_read:
                return {"allowed": True, "code": None, "capability": CAPABILITY_OBSERVE,
                        "reason": "coordinator_evidence_read"}
    if role != "worker":
        return {"allowed": False, "code": CAPABILITY_NOT_GRANTED,
                "capability": required, "reason": "role_not_granted"}
    if required not in capabilities:
        return {"allowed": False, "code": CAPABILITY_NOT_GRANTED,
                "capability": required, "reason": "capability_not_granted"}
    return {"allowed": True, "code": None, "capability": required, "reason": None}
