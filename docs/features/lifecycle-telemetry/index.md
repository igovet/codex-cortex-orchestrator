# Lifecycle telemetry hooks

<!-- GENERATED:START -->

## Purpose

Hooks observe native session, worker, and tool lifecycle events and inject a
bounded authorized context. They are observational only: the SQLite v15
ledger owns attempts, `AttemptEvent`, `AttemptResult`, read observations, and
terminal state.

## Key files

- [hooks.json](../../../plugins/cortex/hooks/hooks.json) registers the six lifecycle hooks.
- [cortex_hook.py](../../../plugins/cortex/scripts/cortex_hook.py) binds native identities and records observations.
- [profiles.json](../../../plugins/cortex/profiles.json) is the profile source.
- [ledger_db.py](../../../plugins/cortex/scripts/cortex_runtime/ledger_db.py) owns canonical state.
- [context_compiler.py](../../../plugins/cortex/scripts/cortex_runtime/context_compiler.py) builds bounded context.

## Behavior

`SessionStart` and `SubagentStart` bind the exact server-issued task,
dispatch, and native identity. `resume`, `clear`, and `compact` reassert the
recovery instruction and require an explicit inspection call.

`PreToolUse` provides non-blocking coordinator guidance. `PostToolUse` records
bounded observations and re-reads canonical state after a wait. `SubagentStop`
records the exact child stop without persisting model-authored prose. `Stop`
prevents a coordinator final answer while a bound worker is still active.

Workers close through `record_attempt_event` and `complete_attempt`. A stop
before `WORK_COMPLETED` enters exact-attempt recovery. Once the canonical
result is `WORK_COMPLETED`, finalization retries that same attempt; no new
worker is created for a view or infrastructure failure.

Successful `read_dispatch_briefing` and assigned predecessor
`read_worker_result` calls create server-owned, idempotent read observations.
Hooks do not accept textual markers as evidence and do not persist raw host
responses, credentials, tokens, or private worker diagnostics.

## Privacy and bounds

Telemetry is bounded by event count and byte budgets, rejects symlink and
non-regular targets, and fails open when an optional observation cannot be
recorded. Ambiguous sessions are rejected rather than guessed. Context
injection contains only the authorized projection for the matching task and
attempt.

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
