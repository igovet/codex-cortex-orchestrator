# Orchestration ledger and attempt lifecycle

<!-- GENERATED:START -->

## Purpose

Cortex 11.0.1 is a database-centric orchestration control plane for the v11
task and capability contract. Schema v17 separates worker semantic input from
server-observed attempt state.

## Key files

- [cortex.py](../../../plugins/cortex/scripts/cortex.py) is the executable facade.
- [ledger_db.py](../../../plugins/cortex/scripts/cortex_runtime/ledger_db.py) owns schema v17, attempts, events, results, observations, governance, projections, private repair escrow, and tombstones.
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

An active non-invalidated dispatch with no finalized canonical result is a
recoverable pending state, never completion evidence. It blocks gate pass,
generic handoff, terminal acceptance, and coordinator stop completion. The
native child binding is useful only to recover the exact attempt; the
coordinator must read the canonical result and receive its server-derived
continuation before closing that child or presenting completion.

## Public operations

The public union is exactly nine operations. Coordinator tasks expose
`start_orchestration`, `continue_orchestration`, `manage_orchestration`,
`manage_governance`, and `read_worker_result`. Worker tasks expose
`worker_question`, `record_attempt_event`, `complete_attempt`,
`read_dispatch_briefing`, and `read_worker_result`.

Every task-scoped call carries the exact opaque `task_ref`; no API chooses a
task by scanning project directories. Coordinator calls also carry the exact
`coordinator_ref`; worker calls carry the exact `assignment_ref`.
Cortex issues those refs; callers only preserve and serialize the exact returned
bytes and never infer a missing value from a host, session, thread, path, or
native child identity.

`start_orchestration` is the sole task creator and initial coordinator
capability issuer. Native execution is only the exact server-issued
`spawn_agent` → `wait` → `read_worker_result` → server-derived continuation
route. Session/environment identity, `create_thread`, server-owned
CLI/executor launches, `repair_planning`, and manually authored
`advance`/`completions` are not public contracts. All plan and outcome repair is
a digest- and capsule-bound patch through `complete_attempt`; lost capability
fails closed. Legacy rows are quarantined rather than imported.

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

## Closed v11 response contract

The nine operations expose closed, typed response unions. Lifecycle responses
use a typed `action` union and a route-specific dispatch, wait, question,
approval, handoff, or top-level `error` + `recovery` branch. Governance responses expose
only a typed receipt, an explicit-inspect typed inspection, or top-level `error` + `recovery`.
Ordinary responses never include generic `user_message`, `user_view`,
`internal`, full pipeline/governance state, or prose `next_action` fields.

Worker responses remain minimal: briefing reads carry bounded content framing;
questions carry a typed question; accepted events carry only the minimal
success acknowledgement; successful completions are terminal acknowledgements with
no `attempt_result_ref`, and the worker final message is exactly
`ATTEMPT_COMPLETED`; result reads carry the compact semantic result plus a
typed continuation or continuation reason. Coordinator result reads use
`task_ref`, `coordinator_ref`, and `step`; the server derives the current wave
and the child never transports a result reference to its parent. Error and recovery branches preserve patch-critical
diagnostic codes, original JSON Pointer paths, exact semantic repair pointers,
bounded nested field schemas, `error={code,category,message,diagnostics}`, and
`recovery={kind,operation,retryable,state_mutated}`. Repair recovery carries
`allowed_ops`, signed opaque repair handles, base payload digests,
and allowed patch paths. Malformed handle copies reissue the
same immutable repair; a correctly shaped handle with a failed integrity check
remains terminal. Recovery responses explicitly report that canonical state was not mutated;
creating or reusing the private immutable repair-escrow row is permitted and
never becomes public evidence. Heavy state is available only through an
explicit inspect operation. A caller uses only the advertised public tool
schema and structured `error`/`recovery` card; it fails closed rather than
searching plugin source, caches, ledger data, sessions, or environment state.
`same_operation` is available only when the response or an already-held
canonical server contract provides explicit `allowed_changes` and makes a
deterministic legal retry possible. A
`terminal_stop` recovery carries action `none`, never a retry, inspection, or
continuation action.

The first bounded briefing page may be incomplete. A worker repeats
`read_dispatch_briefing` with the returned opaque `next_cursor` and its exact
worker pair until `complete=true` before project work. A durable scalar answer
or stable-option selection resumes only the same paused child, whose first
worker call is the exact scalar `worker_question(action:"poll", task_ref,
assignment_ref, question_ref)` form; do not remove and recreate a question to
work around a ref mismatch.

Bootstrap repair is limited to one same-child follow-up that byte-copies the
server-built `bootstrap_repair_message` unchanged. A failed repair is terminally
cleaned up through `finalize_bootstrap_failure`.
An exact nonretryable child final is closed through
`finalize_worker_failure` for the original structured dispatch. That atomic
transition blocks the task and closes the attempt/session nonresumably while
preserving briefing, event, and repair evidence; it emits no result or
replacement dispatch.

## Historical compatibility boundary

Exact canonical schema v16 is the only recognized historical predecessor and
is not migration input. If that v16 namespace is encountered, Cortex
quarantines the entire
namespace—database, sidecars, task and lane files, coordination files, and
lifecycle key—and creates a clean v17 namespace. No row, migration, task,
lane, sidecar, or capability is adopted into the current ledger. V15 and older
or unknown identities fail closed without archival.

## Verification

See [verification.md](../../project/verification.md),
[storage-classification.md](../../project/storage-classification.md), and
[gotchas.md](../../project/gotchas.md). Lifecycle checks are in
[lifecycle telemetry](../lifecycle-telemetry/index.md).

<!-- GENERATED:END -->
