# Lifecycle telemetry hooks

<!-- GENERATED:START -->

## Purpose

Hooks observe native session, worker, and tool lifecycle events and may inject
bounded, identity-free guidance. Model-visible hook output is telemetry only.
Separately, host MCP thread metadata plus trusted local
Host-owned identity binding joins the first authorized worker call to its exact
native child in the root session. `SubagentStop` records exact terminal
completion.
Explicit capability-scoped public operations still own attempts,
`AttemptEvent`, `AttemptResult`, reads, and terminal task state.

## Key files

- [hooks.json](../../../plugins/cortex/hooks/hooks.json) registers the native lifecycle hooks.
- [cortex_hook.py](../../../plugins/cortex/scripts/cortex_hook.py) classifies sanitized, identity-free telemetry and emits only Codex-schema-approved guidance.
- [profiles.json](../../../plugins/cortex/profiles.json) is the profile source.
- [ledger_db.py](../../../plugins/cortex/scripts/cortex_runtime/ledger_db.py) owns canonical state.
- [context_compiler.py](../../../plugins/cortex/scripts/cortex_runtime/context_compiler.py) builds bounded context.

## Behavior

`SessionStart` may remind a coordinator to preserve its already-held private
capability through resume, clear, or compact. `SubagentStart` may return only
its event name plus optional `additionalContext`; `SubagentStop` and `Stop`
return `{}`. Private lifecycle details never cross that output boundary.

The native `wait_agent` operation is generic and timeout-bounded; it has no
exact-child target and its response is not lifecycle evidence. An early,
timed-out, steered, partial, or unrelated wake-up authorizes no result read or
continuation and requires another generic wait while any wave member remains
live. Native child prose and wait output are never parsed for identity, question
authority, terminal status, or completion.

This is a trusted local observation inside the same-user local-state boundary,
not cryptographic proof or server attestation. Malicious same-user modification
of the plugin or its private database is outside the supported threat model.
Unknown hook identity, disabled execution, missing trust, or unverifiable state
fails closed: canonical wave reads and continuation stay unavailable.

Workers checkpoint through `record_attempt_event` and close through
`submit_attempt` or the server-issued `repair_attempt` route. A stop
before `WORK_COMPLETED` is only telemetry; the coordinator follows the exact
server-derived wait or recovery route for its already-bound child. Once the
canonical result is `WORK_COMPLETED`, finalization retries that same attempt;
no new worker is created for a view or infrastructure failure.

After successful finalization, the worker makes no further task-scoped Cortex
calls and the coordinator continues 300-second generic wait cycles for ordinary
progress. `SubagentStop` is the exact terminal host authority. Once every bound
child has a canonical terminal result and matching terminal Stop, the canonical
wave read is available. Generic wait output is progress only and never
lifecycle evidence.

A worker first operation may precede the trusted native spawn observation. For
that pending condition only, retry the same operation with bounded backoff until
a finite deadline, without project access, operation switching, or replacement.
A successful exact retry automatically clears the transient observer failure.
At the deadline follow public fail-closed recovery.

Successful briefing, canonical wave, and assigned predecessor-result reads
create server-owned, idempotent read observations.
Textual markers are not host lifecycle evidence. Hooks do not expose raw host
responses, credentials, tokens, or private worker diagnostics.

Live-proof review uses a bounded private host-metadata audit containing only
lifecycle event classes, equality outcomes, and nonreversible digests. It never
contains raw host identities, payloads, messages, transcripts, paths, reports,
or capabilities. The audit is not public or model-visible and grants no
lifecycle or recovery authority.

## Privacy and bounds

Hook output is bounded, sanitized JSON and never contains a capability, task
identity, assignment identity, briefing path, ledger path, raw host response,
or an extension field outside the registered event's Codex schema. Ambiguous
or missing state produces neutral fail-closed guidance; it is never guessed
from a session, environment, path, database, or thread.

## Installation and verification

Every registered command resolves the bundled `scripts/cortex-launcher` and
`cortex_hook.py`. The launcher selects `CORTEX_PYTHON` or Python 3.11+ from
`PATH`. A stale cache path fails open with exit 0 and `{}`; start a fresh task
after an install or update.

Lifecycle regressions are covered by
[test_marketplace_release_gate.py](../../../tests/test_marketplace_release_gate.py) and the
commands in [verification.md](../../project/verification.md). Source and
tests are authoritative if this generated page drifts.

<!-- GENERATED:END -->
