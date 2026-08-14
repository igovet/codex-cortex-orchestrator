# Lifecycle telemetry hooks

<!-- GENERATED:START -->
## Purpose

The plugin wires session, subagent, and agent-tool lifecycle events to privacy-preserving telemetry and worker-context injection.

## Key files and dependencies

- [hooks.json](../../../plugins/cortex/hooks/hooks.json) registers `SessionStart`, `SubagentStart`, `SubagentStop`, and `PostToolUse` hooks.
- [cortex_hook.py](../../../plugins/cortex/scripts/cortex_hook.py) validates known profile names, emits internal-worker guidance for `SubagentStart`, reasserts the root coordinator lock at `SessionStart`, and records bounded lifecycle events for an active task.
- [profiles.json](../../../plugins/cortex/profiles.json) is the profile-name source used by the hook.

## Behavior and status

Telemetry is bounded to 1,000 events and 256 KiB, keeps a dropped-event count, and writes with regular-file and symlink checks. The hook emits an empty object unless the thread has both an active-task mapping and a matching activation bound to an initialized task. For a matching active task, `SessionStart` injects the coordinator lock: the main/root agent may coordinate, dispatch, wait, evaluate reports, and communicate, but every target-project inspection, edit, build, test, and command belongs to workers. The root remains idle while workers run and cannot treat worker delay or failure as permission to take over project work.

Every registered lifecycle command checks that `${PLUGIN_ROOT}/scripts/cortex_hook.py` still exists before invoking Python. If an already-open thread retains a retired cachebusted plugin path after an update, the command fails open with exit 0 and the empty JSON object `{}`, without stderr, instead of emitting a missing-file error. That protects task completion from stale hook paths but does not load updated skills, hooks, or MCP tools; operators should still start a new thread after updating Cortex. Lifecycle telemetry remains observational and is not proof that a host spawned a worker.

## Verification

Lifecycle-hook regressions are in [test_cortex_invariants.py](../../../tests/test_cortex_invariants.py) and run through the standard unittest command in [verification.md](../../project/verification.md). Coverage executes all four registered commands with a missing `PLUGIN_ROOT` target and requires exit 0, stdout `{}`, and empty stderr.
<!-- GENERATED:END -->
