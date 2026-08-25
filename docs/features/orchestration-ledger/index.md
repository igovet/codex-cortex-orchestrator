# Orchestration ledger and attempt lifecycle

<!-- GENERATED:START -->

## Purpose

Cortex 11.0.1 is a database-centric orchestration control plane for the v11
task and capability contract. Schema v18 separates worker semantic input from
server-observed attempt state.

## Key files

- [cortex.py](../../../plugins/cortex/scripts/cortex.py) is the executable facade.
- [ledger_db.py](../../../plugins/cortex/scripts/cortex_runtime/ledger_db.py) owns schema v18, attempts, events, results, observations, governance, projections, private repair escrow, and tombstones.
- [orchestration_engine.py](../../../plugins/cortex/scripts/cortex_runtime/orchestration_engine.py) owns waves, transitions, and dispatch assembly.
- [context_compiler.py](../../../plugins/cortex/scripts/cortex_runtime/context_compiler.py) compiles complete task context.
- [handoff_compiler.py](../../../plugins/cortex/scripts/cortex_runtime/handoff_compiler.py) builds target-specific handoffs.
- [governance.py](../../../plugins/cortex/scripts/cortex_runtime/governance.py) owns governance state.
- [projection_service.py](../../../plugins/cortex/scripts/cortex_runtime/projection_service.py) materializes rebuildable views.

## Canonical state model

```text
Task intent → ContextCompiler → immutable briefing → worker attempt
                                      │
                    AttemptEvent* → AttemptResult
                                      │
                         server observations
                                      │
                         HandoffCompiler → next target
```

The worker completion contains compact semantic result data. Events are
append-only and bounded. Cortex adds identity, task revision, dispatch,
profile, phase, predecessor scope, timestamps, workspace observations,
executed checks, and verification metadata.

## Lifecycle and recovery

Workers call `record_attempt_event` zero or more times and then
`submit_attempt`; a server-issued same-attempt correction uses
`repair_attempt`. A successful close commits `WORK_COMPLETED`; the server
then performs `FINALIZING` and reaches `COMPLETED` only after required views
and handoffs finish. `BLOCKED` and `FAILED` are explicit semantic outcomes.

Submission and repair are separate MCP tools. Each owns one complete closed
one-level `inputSchema`, and runtime validation uses that same advertised
schema. No action selector, branch registry, compatibility alias, or copied
skill/prompt schema exists. Validation correction remains same-attempt and
never authorizes inspection of private Cortex implementation or state.

```text
RUNNING → WORK_COMPLETED → FINALIZING → COMPLETED
                  │               │
                  └───────────────┴─ retry the same attempt
```

Transport, serialization, view, and projection failures after
`WORK_COMPLETED` never authorize a replacement worker. A lost native
server receipt is recovered from the canonical result.

An active non-invalidated dispatch with no finalized canonical result is a
recoverable pending state, never completion evidence. It blocks gate pass,
generic handoff, terminal acceptance, and coordinator stop completion. The
native child binding is useful only to recover the exact attempt; the
coordinator must read the canonical result and receive its server-derived
continuation before closing that child or presenting completion.

## Public operations

The public facade is action-specific across lifecycle, inspection, recovery,
user interaction, approval, follow-up, steering, artifacts, lanes/resources,
governance, attempt submission/repair, briefing, wave reads, and predecessor
reads. `tools/list` is the authoritative inventory; prose does not maintain a
duplicate tool registry.

Coordinator calls carry private task authority and worker calls carry only
exact native dispatch authority; no API chooses a task by scanning project directories.
Cortex issues that authority; callers only preserve and serialize the exact returned
bytes and never infer a missing value from a host, session, thread, path, or
native child identity.

`start_orchestration` is the sole task creator and initial coordinator
capability issuer. Native execution is only the exact server-issued
`spawn_agent` → exact `wait` → action-specific canonical wave read → server-derived continuation
route. Session/environment identity, `create_thread`, server-owned
CLI/executor launches, `repair_planning`, and manually authored
`advance`/`completions` are not public contracts. The coordinator model owns
the worker waves passed to `start_orchestration`; Cortex validates,
persists, and dispatches it, but does not choose an alternate pipeline or
reconstruct workers. All submitted-report repair is a digest- and
capsule-bound correction through `repair_attempt`; lost capability fails closed.
Semantic content is compact language-neutral text/report data. Server-owned
locale copy or canonical fallback presents approvals and questions, so no
language can block a task.

## Context, handoff, and result links

`ContextCompiler` uses canonical task intent, requirements, decisions, scope,
allowed paths, acceptance/verification criteria, validated predecessor result
references, and server observations. `HandoffCompiler` projects only the
fields needed by implementation, QA, or review. Cross-stage links are
server-derived result links and assignment-granted predecessor context.

Result views, journals, plans, and indexes are rebuildable and cannot authorize
state transitions. SQLite commits canonical state before materializing any
filesystem view.

## Closed v11 response contract

Every action-specific operation exposes its closed v11 response contract.
Worker responses remain minimal; successful completion is terminal, the final
worker message is exactly `ATTEMPT_COMPLETED`, and the child never transports a
result reference to its parent. Heavy state is read only through the relevant
inspection tool.

Errors and recovery provide the bounded structured data needed for the named
next operation. A caller uses only the advertised MCP contract and returned
recovery, never plugin source, caches, ledger data, sessions, environment state,
or hidden paths. Same-attempt repair reuses immutable private escrow and never
turns that escrow into public evidence.

Briefings may require more than one page. Every growing read follows its exact
opaque `c11p` cursor with unchanged authority. Fixed receipts and atomic repair
cards do not paginate. Durable questions and answers are arbitrary-Unicode
text; the coordinator shows and records that text and the worker LLM
interprets adequacy without a structured-choice or localization schema.

Dispatch-authority recovery is limited to the server-returned same-child route.
A failed recovery follows the returned terminal cleanup and never authorizes a
replacement.
An exact nonretryable child final is status text only. Cortex authorizes
`finalize_worker_failure` through structured
`recovery.terminal_failure.evidence="server_bound"` backed by private current
task/attempt/dispatch/generation evidence. The finalizer verifies and consumes
that evidence atomically before blocking the task and closing the
attempt/session nonresumably; missing, stale, wrong-dispatch, or replayed
evidence rejects without a result, replacement, or lifecycle mutation.

## Historical compatibility boundary

Only the exact signed V11 v1--v8 database lineage is migration input. Cortex
validates that complete lineage and upgrades it atomically to schema v18. Any
old task authority is retained only as private, non-selectable migration
state; it is not a public reference choice or a fallback identity. Missing,
unsigned, reordered, tampered, or otherwise unknown histories fail closed and
are never silently quarantined, adopted, or treated as fresh state.

## Verification

See [verification.md](../../project/verification.md),
[storage-classification.md](../../project/storage-classification.md), and
[gotchas.md](../../project/gotchas.md). Lifecycle checks are in
[lifecycle telemetry](../lifecycle-telemetry/index.md).

<!-- GENERATED:END -->
