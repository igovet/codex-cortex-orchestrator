# Lifecycle telemetry hooks

<!-- GENERATED:START -->
## Purpose

The plugin wires session, subagent, and agent-tool lifecycle events to privacy-preserving telemetry and worker-context injection.

## Key files and dependencies

- [hooks.json](../../../plugins/cortex/hooks/hooks.json) registers `SessionStart`, `SubagentStart`, `SubagentStop`, `PreToolUse`, and `PostToolUse` hooks.
- [cortex_hook.py](../../../plugins/cortex/scripts/cortex_hook.py) validates known profile names, emits internal-worker guidance for `SubagentStart`, reasserts the root coordinator lock at `SessionStart`, and records bounded lifecycle events for an active task.
- [profiles.json](../../../plugins/cortex/profiles.json) is the profile-name source used by the hook.

## Behavior and status

After a v5 start or corrective follow-up, the synchronous `PostToolUse` hook
binds the returned task to the documented event `session_id` without changing
task authorization. It resolves the project from MCP `tool_input.project_root`
or event `cwd`; explicitly forwarded session environment values are only
compatibility fallbacks. The hook resolves `SessionStart` identity from the
documented `session_id` field and accepts legacy `thread_id`. Hook context is
returned in the documented `hookSpecificOutput.additionalContext` envelope.
`resume`, `clear`, and `compact` reassert recovery, and `read_worker_report`
PostToolUse context repeats the exact link that must be shown in the main chat.
After a successful start or recovery response containing pending dispatches,
PostToolUse also places a compact `CORTEX DISPATCH REQUIRED NOW` instruction
after the MCP payload: the next native call must spawn the first returned
dispatch, and waiting or another lifecycle call is forbidden until every
authorized spawn returns a child id. This prevents a long result from leaving
the actionable dispatch below the model's effective attention boundary.
Before an `Agent` call, `PreToolUse` denies a targetless wait when Cortex has no
durably bound running child, so the host cannot block indefinitely while
claiming that a worker was dispatched. The denial is the actionable
`CORTEX DISPATCH FAILURE` diagnostic: it says that no worker was spawned,
requires invoking the exact pending `spawn_agent` dispatch, and forbids retrying
an empty wait. A host wait-any representation may use an empty receiver list
only while such a child exists. The PostToolUse form remains a compatibility
fallback.
Installing or reloading the
plugin remains an operator action; after an install or update, start a fresh
Codex thread so the new hook and skill paths are loaded.

Telemetry is bounded to 1,000 events and 256 KiB, keeps a dropped-event count, and writes with regular-file and symlink checks. The hook emits an empty object unless the thread has both an active-task mapping and a matching activation bound to an initialized task. For a matching active task, `SessionStart` injects the coordinator lock: the main/root agent may coordinate, dispatch, wait, evaluate reports, and communicate, but every target-project inspection, edit, build, test, and command belongs to workers. The root remains idle while workers run and cannot treat worker delay or failure as permission to take over project work. A `resume`/`clear`/`compact` start also injects a durable recovery instruction with the registry-backed opaque `task_ref`, requiring one `manage_orchestration(intent="inspect")` call and forbidding duplicate starts or replayed dispatches.
`SubagentStart` uses the documented parent `session_id`, opaque child id,
actual model, and exact returned dispatch identity to bind the exact running
attempt; dynamically named workers report generic
`agent_type=default`, not their native task key. Compaction
recovery can therefore distinguish an unstarted dispatch from an active child
and wait only on the exact preserved child id.
If the host-session binding is missing, recovery is allowed only when exactly
one active pending dispatch matches the exact native task key, or when the
generic `default` event matches exactly one pending dispatch and its observed
model. Missing, ambiguous, already-consumed, or model-mismatched identities
fail closed; the hook never guesses a child or reuses a previous session.
`SubagentStop` updates the exact child-bound attempt without persisting the
model-authored final text: an already recorded report is exposed for
continuation, an open durable question remains resumable, and every other stop
is failed as `native_worker_stopped_without_report`. Recovery lists these as
non-waitable `stopped_workers`, never as active children. Because the host does
not allow `SubagentStop` to emit model context, the supported `PostToolUse`
hook on the completing wait re-reads this durable state. When the latest
attempt stopped without a report it instructs the coordinator to inspect and
submit the exact failed result, and explicitly forbids a corrective
`followup_task` to the dead child. The failed continuation is bounded by
`MAX_ORCHESTRATE_GATE_FAILURES = 3`: only a newly returned top-level dispatch
may retry, and the third failure blocks the task with a durable handoff.
If more than one active task shares a host session, Cortex removes the session's
lookup entry until only one active task remains; the hook never guesses which
task should receive recovery context. Completing one of the ambiguous tasks
rebuilds the entry when exactly one active task is left.

This terminal stop path closes the session-start/pre-planning dispatch failure
where a native child had already stopped but recovery still treated it as a
follow-up target. The coordinator must inspect once, submit the stopped
attempt's exact `dispatch_ref` as a failed continuation, and spawn only the new
top-level dispatch returned by `continue_orchestration`; an empty wait or
`followup_task` against the dead child is rejected as an invalid recovery.

Every registered lifecycle command checks that `${PLUGIN_ROOT}/scripts/cortex-launcher` and `${PLUGIN_ROOT}/scripts/cortex_hook.py` still exist before invoking the launcher. The launcher selects `CORTEX_PYTHON` (or `python3` from `PATH` when unset) and requires Python 3.11+ with `tomllib`. If an already-open thread retains a retired cachebusted plugin path after an update, the command fails open with exit 0 and the empty JSON object `{}`, without stderr, instead of emitting a missing-file error. That protects task completion from stale hook paths but does not load updated skills, hooks, or MCP tools; operators should still start a new thread after updating Cortex. Lifecycle telemetry remains observational and is not proof that a host spawned a worker.

The installer validates this boundary separately from task telemetry. After an
explicit sync it queries Codex `hooks/list` and accepts only the exact five
enabled `cortex@cortex` hooks whose source is the installed cache and whose
command invokes that cache's launcher and `cortex_hook.py`. It writes their exact SHA-256
content hashes to the global hook-state configuration; `sync-cortex.sh
--check` revalidates the plugin id, source path, command path, enabled state,
hash shape, and complete five-hook set. This prevents a changed or untrusted
`PreToolUse`/`PostToolUse` hook from silently breaking durable worker binding.
The host still needs a new thread after installation or update to load the new
hook paths.

## Verification

Lifecycle-hook regressions are in [test_cortex_invariants.py](../../../tests/test_cortex_invariants.py) and run through the standard unittest command in [verification.md](../../project/verification.md). The session-start/pre-planning recovery path is covered by the focused command below, which exercises terminal reportless stops, exact failed continuation, bounded retry, and empty-wait rejection:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v \
  tests.test_revision_aware_epic.RevisionAwareEpicAcceptanceTests.test_reportless_subagent_stop_is_terminal_and_non_resumable \
  tests.test_revision_aware_epic.RevisionAwareEpicAcceptanceTests.test_reportless_plan_stop_requires_failed_receipt_before_retry \
  tests.test_revision_aware_epic.RevisionAwareEpicAcceptanceTests.test_mixed_wave_reportless_stop_keeps_failed_slot_addressable \
  tests.test_revision_aware_epic.RevisionAwareEpicAcceptanceTests.test_repeated_reportless_stops_use_three_failure_budget \
  tests.test_cortex_control.ControlPlaneTests.test_post_wait_stop_context_directs_terminal_failure \
  tests.test_cortex_invariants.OrchestrationInvariantTests.test_agent_hook_rejects_empty_wait_as_unspawned_dispatch
```

Coverage executes all four registered commands with a missing `PLUGIN_ROOT` target and requires exit 0, stdout `{}`, and empty stderr.
<!-- GENERATED:END -->
