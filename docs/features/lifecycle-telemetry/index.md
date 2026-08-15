# Lifecycle telemetry hooks

<!-- GENERATED:START -->
## Purpose

The plugin wires session, subagent, and agent-tool lifecycle events to privacy-preserving telemetry and worker-context injection.

## Key files and dependencies

- [hooks.json](../../../plugins/cortex/hooks/hooks.json) registers `SessionStart`, `SubagentStart`, `SubagentStop`, and `PostToolUse` hooks.
- [cortex_hook.py](../../../plugins/cortex/scripts/cortex_hook.py) validates known profile names, emits internal-worker guidance for `SubagentStart`, reasserts the root coordinator lock at `SessionStart`, and records bounded lifecycle events for an active task.
- [profiles.json](../../../plugins/cortex/profiles.json) is the profile-name source used by the hook.

## Behavior and status

After a v3 start or corrective follow-up, the synchronous `PostToolUse` hook
binds the returned task to the documented event `session_id` without changing
task authorization. It resolves the project from MCP `tool_input.project_root`
or event `cwd`; explicitly forwarded session environment values are only
compatibility fallbacks. The hook resolves `SessionStart` identity from the
documented `session_id` field and accepts legacy `thread_id`. Hook context is
returned in the documented `hookSpecificOutput.additionalContext` envelope.
`resume`, `clear`, and `compact` reassert recovery, and `read_worker_report`
PostToolUse context repeats the exact link that must be shown in the main chat.
Installing or reloading the
plugin remains an operator action; after an install or update, start a fresh
Codex thread so the new hook and skill paths are loaded.

Telemetry is bounded to 1,000 events and 256 KiB, keeps a dropped-event count, and writes with regular-file and symlink checks. The hook emits an empty object unless the thread has both an active-task mapping and a matching activation bound to an initialized task. For a matching active task, `SessionStart` injects the coordinator lock: the main/root agent may coordinate, dispatch, wait, evaluate reports, and communicate, but every target-project inspection, edit, build, test, and command belongs to workers. The root remains idle while workers run and cannot treat worker delay or failure as permission to take over project work. A `resume`/`clear`/`compact` start also injects a durable recovery instruction with the registry-backed opaque `task_ref`, requiring one `manage_orchestration(intent="inspect")` call and forbidding duplicate starts or replayed dispatches.
If more than one active task shares a host session, Cortex removes the session's
lookup entry until only one active task remains; the hook never guesses which
task should receive recovery context. Completing one of the ambiguous tasks
rebuilds the entry when exactly one active task is left.

Every registered lifecycle command checks that `${PLUGIN_ROOT}/scripts/cortex_hook.py` still exists before invoking Python. If an already-open thread retains a retired cachebusted plugin path after an update, the command fails open with exit 0 and the empty JSON object `{}`, without stderr, instead of emitting a missing-file error. That protects task completion from stale hook paths but does not load updated skills, hooks, or MCP tools; operators should still start a new thread after updating Cortex. Lifecycle telemetry remains observational and is not proof that a host spawned a worker.

## Verification

Lifecycle-hook regressions are in [test_cortex_invariants.py](../../../tests/test_cortex_invariants.py) and run through the standard unittest command in [verification.md](../../project/verification.md). Coverage executes all four registered commands with a missing `PLUGIN_ROOT` target and requires exit 0, stdout `{}`, and empty stderr.
<!-- GENERATED:END -->
