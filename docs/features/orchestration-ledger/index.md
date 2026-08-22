# Orchestration ledger and attempt lifecycle

<!-- GENERATED:START -->

## Purpose

Cortex 10.0.0 is a database-centric orchestration control plane for the
`cortex/v10` task contract and `cortex/orchestration/v6` lifecycle. Schema v15
separates worker semantic input from server-observed attempt state.

## Key files

- [cortex.py](../../../plugins/cortex/scripts/cortex.py) is the executable facade.
- [ledger_db.py](../../../plugins/cortex/scripts/cortex_runtime/ledger_db.py) owns schema v15, attempts, events, results, observations, governance, projections, and tombstones.
- [orchestration_engine.py](../../../plugins/cortex/scripts/cortex_runtime/orchestration_engine.py) owns waves, transitions, and dispatch assembly.
- [context_compiler.py](../../../plugins/cortex/scripts/cortex_runtime/context_compiler.py) compiles bounded task context.
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

The worker result contains `status`, `summary`, `findings`,
`decisions_needed`, `unresolved`, and optional typed `claims`. Events are
append-only and bounded. Cortex adds identity, task revision, dispatch,
profile, phase, predecessor scope, timestamps, workspace observations,
executed checks, and verification metadata.

## Lifecycle and recovery

Workers call `record_attempt_event` zero or more times and then
`complete_attempt`. A successful close commits `WORK_COMPLETED`; the server
then performs `FINALIZING` and reaches `COMPLETED` only after required views
and handoffs finish. `BLOCKED` and `FAILED` are explicit semantic outcomes.

```text
RUNNING → WORK_COMPLETED → FINALIZING → COMPLETED
                  │               │
                  └───────────────┴─ retry the same attempt
```

Transport, serialization, view, and projection failures after
`WORK_COMPLETED` never authorize a replacement worker. A lost native
server receipt is recovered from the canonical result.

## Public operations

The public union is exactly nine operations. Coordinator tasks expose
`start_orchestration`, `continue_orchestration`, `manage_orchestration`,
`manage_governance`, and `read_worker_result`. Worker tasks expose
`worker_question`, `record_attempt_event`, `complete_attempt`,
`read_dispatch_briefing`, and `read_worker_result`.

Every task-scoped call carries the exact opaque `task_ref`; no API chooses a
task by scanning project directories.

## Context, handoff, and result links

`ContextCompiler` uses canonical task intent, requirements, decisions, scope,
allowed paths, acceptance/verification criteria, validated predecessor result
references, and server observations. `HandoffCompiler` projects only the
fields needed by implementation, QA, or review. Cross-stage links are
`attempt_result_ref`, `context_result_refs`, and
`predecessor_result_refs`.

Result views, journals, plans, and indexes are rebuildable and cannot authorize
state transitions. SQLite commits canonical state before materializing any
filesystem view.

## Verification

See [verification.md](../../project/verification.md),
[storage-classification.md](../../project/storage-classification.md), and
[gotchas.md](../../project/gotchas.md). Lifecycle checks are in
[lifecycle telemetry](../lifecycle-telemetry/index.md).

<!-- GENERATED:END -->
