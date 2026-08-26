# Orchestration ledger and attempt lifecycle

<!-- GENERATED:START -->

## Purpose

Cortex 11.0.1 is a database-centric orchestration control plane for the v11
task and capability contract. Schema v19 separates worker semantic input from
server-observed attempt state.

## Key files

- [cortex.py](../../../plugins/cortex/scripts/cortex.py) is the executable facade.
- [ledger_db.py](../../../plugins/cortex/scripts/cortex_runtime/ledger_db.py) owns schema v19, attempts, events, results, observations, governance, projections, private repair escrow, and tombstones.
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
server manifest evidence, exact native Stop, and worker-attested semantic
verification claims.

## Lifecycle and recovery

Workers call `record_attempt_event` zero or more times and then
`submit_attempt`; a server-issued same-attempt correction uses
`repair_attempt`. A successful close commits `WORK_COMPLETED`; the server
then performs `FINALIZING` and reaches `COMPLETED` only after required views
and handoffs finish. `BLOCKED` and `FAILED` are explicit semantic outcomes.

Submission and repair are separate MCP tools. Each owns one complete closed
one-level public contract, and runtime validation uses that same advertised
schema. No action selector, branch registry, compatibility alias, or copied
skill/prompt schema exists. Validation correction remains same-attempt and
never authorizes inspection of private Cortex implementation or state.

```text
RUNNING → WORK_COMPLETED → FINALIZING → COMPLETED
                  │               │
                  └───────────────┴─ retry the same attempt
```

Transport, serialization, view, and projection failures after
`WORK_COMPLETED` never authorize a replacement worker. Interrupted native
progress is resolved from public canonical state and recovery.

After successful terminal submission, the worker makes no further task-scoped
Cortex calls and the coordinator continues 300-second generic `wait_agent`
cycles for ordinary progress. Host
MCP thread metadata plus trusted local `SubagentStart`/`SubagentStop` privately
join the native V2 worker to its dispatch and record the exact terminal Stop.
`SubagentStop` is the exact terminal host authority. Once every bound child has
a canonical terminal result and matching terminal Stop, the canonical read is
available. Generic wait output is progress only and never lifecycle evidence.
This is same-user trusted local
observation, not cryptographic proof or server attestation; unknown or disabled
hook state fails closed.

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
native V2 `spawn_agent` → generic timeout-bounded `wait_agent` cycles →
canonical terminal results plus exact matching terminal Stops →
action-specific canonical wave read and server-derived continuation route.
Session/environment identity, `create_thread`, server-owned
CLI/executor launches, `repair_planning`, and manually authored
`advance`/`completions` are not public contracts. The coordinator model owns
the worker waves passed to `start_orchestration`; Cortex validates,
persists, and dispatches it, but does not choose an alternate pipeline or
reconstruct workers. All submitted-report repair is a digest- and
capsule-bound correction through `repair_attempt`; lost capability fails closed.
Coordinator-authored acceptance and verification arrays retain their normalized
exact order in an immutable result-contract artifact. Separate server baseline
obligations can add but never replace them, and one contract digest binds plan,
assignment, briefing, result evaluation, governance closure, replay, and handoff.
After a wave is canonically read, the coordinator uses
`revise_future_pipeline` only for unexecuted future work and
`append_rework_wave` only for product correction of a completed canonical
result. Technical failure uses server-owned Luna-to-Terra-to-Sol replacement
with capability-safe profile resolution. Every returned worker repeats the
native lifecycle and canonical read barrier, and required governance closure
executes before final handoff.
Semantic content is compact language-neutral text/report data. Server-owned
locale copy or canonical fallback presents approvals and questions, so no
language can block a task.

## Context, handoff, and result links

`ContextCompiler` uses canonical task intent, requirements, decisions, scope,
task boundaries, acceptance/verification criteria, validated predecessor result
references, and server observations. `HandoffCompiler` projects only the
fields needed by implementation, QA, or review. Cross-stage links are
server-derived result links and assignment-granted predecessor context.

Result views, journals, plans, and indexes are rebuildable and cannot authorize
state transitions. SQLite commits canonical state before materializing any
filesystem view.

## Closed v11 response contract

Every action-specific operation exposes its closed v11 response contract.
Worker responses remain minimal; successful completion is terminal, and the
child never transports a result reference or lifecycle evidence to its parent.
Heavy state is read only through the relevant inspection tool. A canonical wave
read and continuation remain unavailable until every bound child has both a
canonical terminal result and exact matching terminal Stop. Generic wait output
never substitutes for terminal `SubagentStop`.

Errors and recovery provide the bounded structured data needed for the named
next operation. A caller uses only the advertised MCP contract and returned
recovery, never plugin source, caches, ledger data, sessions, environment state,
or hidden paths. Same-attempt repair reuses immutable private escrow and never
turns that escrow into public evidence.

Briefings may require more than one page. Every growing read follows its exact
opaque continuation with unchanged authority. Fixed receipts and atomic repair
cards do not paginate. Publishing a durable arbitrary-Unicode question ends the
worker's native turn and genuinely pauses that child. The coordinator shows the
complete question and records the real arbitrary-Unicode answer; only then does
the same child resume in a new native turn and interpret adequacy without a
structured-choice or localization schema.

The transactional V19 migration classifies every integrity-accepted V17/V18
user-facing durable question as `requirement` without text or language
inference and recomputes its category-bound content digest before recording the
V19 migration row. Answered rows also receive the current exact-answer
idempotency digest without changing answer text or submission identity.
Post-migration NULL or internal-category rows are not part of that signed
cutover and cannot authorize a user pause or resume.

If a worker's first operation is pending trusted spawn observation, it retries
only that same operation with bounded backoff until a finite deadline. It makes
no project access, switches no operation, and never spawns a replacement. A
successful exact retry clears the transient observer failure automatically. At
the deadline, or for any other dispatch-authority failure, it follows only public
fail-closed recovery. A nonretryable child final is status text only; terminal
cleanup requires the public structured route for the original dispatch. Missing,
stale, mismatched, or replayed recovery rejects without a result, replacement,
or lifecycle mutation.

## Historical compatibility boundary

Exact signed released schema-v17 and schema-v18 histories are the in-place
migration inputs. Cortex validates their complete ordered signed lineage,
performs the data cutover transactionally, preserves every append-only released
migration row, and appends schema v19. The exact signed legacy V1--V8 namespace
is instead archived privately before Cortex creates a fresh schema-v19 ledger;
its task authority is not migrated, selectable, or a fallback identity.
Missing, unsigned, reordered, tampered, or otherwise unknown histories fail
closed and are not automatically quarantined, adopted, or treated as fresh state.

## Verification

See [verification.md](../../project/verification.md),
[storage-classification.md](../../project/storage-classification.md), and
[gotchas.md](../../project/gotchas.md). Lifecycle checks are in
[lifecycle telemetry](../lifecycle-telemetry/index.md).

<!-- GENERATED:END -->
