# Architecture decisions

<!-- GENERATED:START -->

## Ordered waves and canonical attempts

The SQLite ledger retains ordered waves so dependencies, retries, and terminal
state are auditable. Every worker execution is one server-issued attempt. The
worker contributes semantic facts through `AttemptEvent` and closes with one
`AttemptResult`; Cortex owns identity, timestamps, dispatch binding, task
revision, workspace observations, manifest reconciliation, and native Stop.
Semantic execution checks remain worker-attested.

## Public facade

The launch-time audience is immutable. The v11 facade exposes one MCP tool per
semantic action across lifecycle, inspection, recovery, interaction, approval,
artifacts, lane/resource control, governance, attempt completion, and scoped
reads. There are no action multiplexers, branch registries, compatibility
aliases, or shared tools with audience-dependent input shapes. `tools/list` is
the authoritative inventory.

Each tool owns one complete closed one-level contract from
`public_contracts.py`; runtime validation consumes that same schema. Tool
descriptions are short semantics. Skills and prompts do not duplicate fields,
constraints, or schema templates.

## Attempt protocol and lifecycle

Worker completion records compact semantic result data.
`AttemptEvent` is an append-only lossless stream for findings, decision
evidence, blockers, worker-attested verification claims, progress, and notes.

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
changed files, workspace manifests, canonical result identity, and exact
native Stop. Command, browser, console, network, accessibility, layout, and
test claims retain worker provenance; their receipt proves only identity,
digest, and storage. A worker-authored assertion cannot turn an unavailable
server observation into a pass. Successful `read_dispatch_briefing` and assigned
`read_predecessor_result` calls create idempotent, task/attempt-scoped SQLite read
observations.

## Context and handoff

`ContextCompiler` builds a complete dispatch context from canonical task intent,
requirements, constraints, decisions, acceptance and verification criteria,
assigned scope, task boundaries, validated predecessor result references, and
server observations. `HandoffCompiler` produces target-specific projections:
implementation receives scope and requirements, QA receives changed files and
verification needs, and review receives the change inventory and open
findings. Raw worker payloads are never used as a mutable universal handoff.

## Projection boundary

Result JSON/Markdown, journals, plans, and indexes are rebuildable views. They
are useful for humans and tooling but cannot authorize a gate, read, resume,
handoff, or completion. A successful worker completion has no result-reference
handoff: the worker ends its task-scoped Cortex calls, and the coordinator
continues generic wait cycles. Host MCP thread metadata plus trusted local
The host binds the first authorized worker call to its exact native child in the
root session. The current wave
remains unreadable until every bound child has a canonical terminal result and
matching terminal `SubagentStop`. `SubagentStop` is the exact terminal host
authority. Generic waits are progress control only and never completion evidence.
The coordinator supplies no lifecycle evidence and never inspects plugin or
private state. This is same-user local trust, not
cryptographic proof or server attestation; unknown or disabled hooks fail
closed. Context and predecessor links remain server-
owned, not values a child transports to its parent.

After a completed wave is read, use `revise_future_pipeline` only for
unexecuted future work and `append_rework_wave` only for product correction of
a completed canonical result. Technical failure uses server-owned
Luna-to-Terra-to-Sol replacement with capability-safe profile resolution.
Every returned worker repeats native dispatch, ordinary generic waits, matching
terminal Stop, and canonical reading; required governance closure executes
before final handoff.

A worker first operation that is pending trusted spawn observation retries only
that same operation with bounded backoff until a finite deadline and performs no
project access. A successful exact retry automatically clears the transient
observer failure. At the deadline, or for any other dispatch-authority failure,
only public fail-closed recovery applies; it never reconstructs worker authority
or spawns a replacement.
An already-started worker's nonretryable final is status text only. The
coordinator uses terminal failure finalization only when public structured
recovery explicitly directs it for the original native dispatch. The finalizer
verifies and consumes the private current binding before blocking the task and
terminalizing the attempt; missing, stale, wrong-dispatch, or replayed recovery
rejects without mutation. Native prose is never parsed into authority.

## Governance and storage

Governance state, immutable artifacts, scope/revision constraints, and
authenticated lifecycle transitions remain server-owned in schema v19. SQLite
is the atomic state boundary; filesystem views are private, digest-checked,
and rebuildable. WAL/SHM files and advisory locks are SQLite machinery, not
application evidence.

## References

- [storage-classification.md](storage-classification.md) defines retention and authority.
- [orchestration ledger](../features/orchestration-ledger/index.md) documents the lifecycle.
- [lifecycle telemetry](../features/lifecycle-telemetry/index.md) documents hooks and recovery.
- [ledger_db.py](../../plugins/cortex/scripts/cortex_runtime/ledger_db.py) owns schema v19.
- [context_compiler.py](../../plugins/cortex/scripts/cortex_runtime/context_compiler.py) and [handoff_compiler.py](../../plugins/cortex/scripts/cortex_runtime/handoff_compiler.py) implement context boundaries.
- [verification.md](verification.md) is the release validation index.

<!-- GENERATED:END -->
