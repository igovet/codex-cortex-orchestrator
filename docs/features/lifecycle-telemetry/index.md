# Lifecycle telemetry hooks

<!-- GENERATED:START -->

## Purpose

Hooks observe native session, worker, and tool lifecycle events and may inject
bounded, identity-free guidance. They are telemetry only: the SQLite v17
ledger and explicit capability-scoped public operations own attempts,
`AttemptEvent`, `AttemptResult`, read observations, and terminal state.

## Key files

- [hooks.json](../../../plugins/cortex/hooks/hooks.json) registers the five lifecycle hooks.
- [cortex_hook.py](../../../plugins/cortex/scripts/cortex_hook.py) emits sanitized, identity-free telemetry guidance.
- [profiles.json](../../../plugins/cortex/profiles.json) is the profile source.
- [ledger_db.py](../../../plugins/cortex/scripts/cortex_runtime/ledger_db.py) owns canonical state.
- [context_compiler.py](../../../plugins/cortex/scripts/cortex_runtime/context_compiler.py) builds bounded context.

## Behavior

`SessionStart` may remind a coordinator to preserve its already-held exact
capability pair through resume, clear, or compact. `SubagentStart`,
`SubagentStop`, and `Stop` emit only neutral native-lifecycle observations.
`PostToolUse` is limited to exact native `spawn_agent`, `wait`, and
`wait_agent` calls. There is no `PreToolUse` hook.

No hook binds a child, carries or reconstructs a capability, reads or writes
the ledger, records an attempt observation, selects a task, authorizes a
replacement, or proves completion. Exact native lifecycle binding and
canonical result processing remain server-owned public operations.

Workers close through `record_attempt_event` and `complete_attempt`. A stop
before `WORK_COMPLETED` is only telemetry; the coordinator follows the exact
server-derived wait or recovery route for its already-bound child. Once the
canonical result is `WORK_COMPLETED`, finalization retries that same attempt;
no new worker is created for a view or infrastructure failure.

Successful `read_dispatch_briefing` and assigned predecessor
`read_worker_result` calls create server-owned, idempotent read observations.
Hooks do not accept textual markers as evidence and do not persist raw host
responses, credentials, tokens, or private worker diagnostics.

## Privacy and bounds

Telemetry output is bounded, sanitized JSON and never contains a capability,
task identity, assignment identity, briefing path, ledger path, or raw host
response. Ambiguous or missing state produces neutral fail-closed guidance;
it is never guessed from a session, environment, path, database, or thread.

## Installation and verification

Every registered command resolves the bundled `scripts/cortex-launcher` and
`cortex_hook.py`. The launcher selects `CORTEX_PYTHON` or Python 3.11+ from
`PATH`. A stale cache path fails open with exit 0 and `{}`; start a fresh task
after an install or update.

Lifecycle regressions are covered by
[test_cortex_invariants.py](../../../tests/test_cortex_invariants.py) and the
commands in [verification.md](../../project/verification.md). Source and
tests are authoritative if this generated page drifts.

<!-- GENERATED:END -->
