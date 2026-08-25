# Architecture decisions

<!-- GENERATED:START -->

## Ordered waves and canonical attempts

The SQLite ledger retains ordered waves so dependencies, retries, and terminal
state are auditable. Every worker execution is one server-issued attempt. The
worker contributes semantic facts through `AttemptEvent` and closes with one
`AttemptResult`; Cortex owns identity, timestamps, dispatch binding, task
revision, workspace observations, checks, and verification observations.

## Public facade

The launch-time audience is immutable. The v11 facade exposes one MCP tool per
semantic action across lifecycle, inspection, recovery, interaction, approval,
artifacts, lane/resource control, governance, attempt completion, and scoped
reads. There are no action multiplexers, branch registries, compatibility
aliases, or shared tools with audience-dependent input shapes. `tools/list` is
the authoritative inventory.

Each tool owns one complete closed one-level `inputSchema` from
`public_contracts.py`; runtime validation consumes that same schema. Tool
descriptions are short semantics. Skills and prompts do not duplicate fields,
constraints, or schema templates.

## Attempt protocol and lifecycle

Worker completion records compact semantic result data.
`AttemptEvent` is an append-only lossless stream for findings, decision
evidence, blockers, verification observations, progress, and notes.

The lifecycle is:

```text
RUNNING → WORK_COMPLETED → FINALIZING → COMPLETED
     ├── BLOCKED
     └── FAILED
```

`WORK_COMPLETED` means the semantic result is durable. `FINALIZING` covers
server-owned result views and handoff work. A projection, serialization, or
infrastructure failure after `WORK_COMPLETED` retries the same attempt and
never starts a replacement worker.

## Server-owned evidence

The server derives native identity, profile, phase, task revision, timestamps,
changed files, executed checks, workspace observations, and verification
metadata. A worker-authored assertion cannot turn an unavailable observation
into a pass. Successful `read_dispatch_briefing` and assigned
`read_predecessor_result` calls create idempotent, task/attempt-scoped SQLite read
observations.

## Context and handoff

`ContextCompiler` builds a complete dispatch context from canonical task intent,
requirements, constraints, decisions, acceptance and verification criteria,
assigned scope, allowed paths, validated predecessor result references, and
server observations. `HandoffCompiler` produces target-specific projections:
implementation receives scope and requirements, QA receives changed files and
verification needs, and review receives the change inventory and open
findings. Raw worker payloads are never used as a mutable universal handoff.

## Projection boundary

Result JSON/Markdown, journals, plans, and indexes are rebuildable views. They
are useful for humans and tooling but cannot authorize a gate, read, resume,
handoff, or completion. A successful worker completion has no result-reference
handoff: the worker returns the terminal acknowledgement and emits exactly
`ATTEMPT_COMPLETED`. The coordinator reads canonical results through its
private authority and the server derives the current wave through server-owned
result links. Context and predecessor links remain server-owned, not values a
child must transport to its parent.

Dispatch-authority recovery is bounded by the server-returned same-child route.
A failed recovery follows the returned terminal cleanup; it never reconstructs
worker authority or spawns a replacement.
An already-started worker's exact nonretryable terminal marker is status text
only. Structured `recovery.terminal_failure.evidence="server_bound"` records
private current task/attempt/dispatch/generation evidence; only then may the
coordinator call `finalize_worker_failure` for the original dispatch. The same
transaction verifies and consumes the evidence, blocks the task, and makes the
attempt/session terminal and nonresumable without an AttemptResult or
replacement. Missing, stale, wrong-dispatch, and replayed evidence cannot
mutate lifecycle state.

## Governance and storage

Governance state, immutable artifacts, scope/revision constraints, and
authenticated lifecycle transitions remain server-owned in schema v18. SQLite
is the atomic state boundary; filesystem views are private, digest-checked,
and rebuildable. WAL/SHM files and advisory locks are SQLite machinery, not
application evidence.

## References

- [storage-classification.md](storage-classification.md) defines retention and authority.
- [orchestration ledger](../features/orchestration-ledger/index.md) documents the lifecycle.
- [lifecycle telemetry](../features/lifecycle-telemetry/index.md) documents hooks and recovery.
- [ledger_db.py](../../plugins/cortex/scripts/cortex_runtime/ledger_db.py) owns schema v18.
- [context_compiler.py](../../plugins/cortex/scripts/cortex_runtime/context_compiler.py) and [handoff_compiler.py](../../plugins/cortex/scripts/cortex_runtime/handoff_compiler.py) implement context boundaries.
- [verification.md](verification.md) is the release validation index.

<!-- GENERATED:END -->
