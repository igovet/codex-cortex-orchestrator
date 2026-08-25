# Architecture decisions

<!-- GENERATED:START -->

## Ordered waves and canonical attempts

The SQLite ledger retains ordered waves so dependencies, retries, and terminal
state are auditable. Every worker execution is one server-issued attempt. The
worker contributes semantic facts through `AttemptEvent` and closes with one
`AttemptResult`; Cortex owns identity, timestamps, dispatch binding, task
revision, workspace observations, checks, and verification observations.

## Public facade

The launch-time audience is immutable. The public union contains exactly nine
operations:

- coordinator: `start_orchestration`, `continue_orchestration`,
  `manage_orchestration`, `manage_governance`, `read_worker_result`;
- worker: `worker_question`, `record_attempt_event`, `complete_attempt`,
  `read_dispatch_briefing`, `read_worker_result`.

There is one current protocol. The two fixed audiences expose only the
operations listed above.

## Attempt protocol and lifecycle

`AttemptResult` contains only worker semantic data: `status`, `summary`,
`findings`, `decisions_needed`, `unresolved`, and optional typed `claims`.
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
into a pass. Successful `read_dispatch_briefing` and assigned predecessor
`read_worker_result` calls create idempotent, task/attempt-scoped SQLite read
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
`ATTEMPT_COMPLETED`. The coordinator reads
`read_worker_result(task_ref, coordinator_ref, step)` and the server derives
the current wave's canonical results. `attempt_result_ref`,
`context_result_refs`, and `predecessor_result_refs` remain server-owned links,
not values a child must transport to its parent.

Bootstrap recovery is bounded to one same-child follow-up using the exact
server-built `bootstrap_repair_message` byte-for-byte. A failed repair is
closed by `finalize_bootstrap_failure`, which performs terminal cleanup.
An already-started worker's exact nonretryable terminal marker is closed by
`finalize_worker_failure` for its original structured dispatch. The same
transaction blocks the task and makes the attempt/session terminal and
nonresumable without deleting briefing, event, or repair evidence and without
creating an AttemptResult or replacement.

## Governance and storage

Governance state, immutable artifacts, scope/revision constraints, and
authenticated lifecycle transitions remain server-owned in schema v17. SQLite
is the atomic state boundary; filesystem views are private, digest-checked,
and rebuildable. WAL/SHM files and advisory locks are SQLite machinery, not
application evidence.

## References

- [storage-classification.md](storage-classification.md) defines retention and authority.
- [orchestration ledger](../features/orchestration-ledger/index.md) documents the lifecycle.
- [lifecycle telemetry](../features/lifecycle-telemetry/index.md) documents hooks and recovery.
- [ledger_db.py](../../plugins/cortex/scripts/cortex_runtime/ledger_db.py) owns schema v17.
- [context_compiler.py](../../plugins/cortex/scripts/cortex_runtime/context_compiler.py) and [handoff_compiler.py](../../plugins/cortex/scripts/cortex_runtime/handoff_compiler.py) implement context boundaries.
- [verification.md](verification.md) is the release validation index.

<!-- GENERATED:END -->
