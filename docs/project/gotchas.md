# Gotchas

## Canonical state and views

- cortex.db schema v19 is authoritative. AttemptResult and append-only AttemptEvent rows are the worker protocol; private repair escrow retains rejected drafts, while JSON, Markdown, journals, and indexes are views.
- Do not repair SQLite state by editing a view. Retry the responsible projection job.
- WAL/SHM files and .state.lock are SQLite machinery. Never attach them as evidence.
- A failed serializer, view, or infrastructure operation after WORK_COMPLETED retries the same attempt. A second worker would duplicate work.

## Attempt lifecycle

- WORK_COMPLETED means the semantic result is durable; FINALIZING and COMPLETED are distinct server states.
- BLOCKED and FAILED are semantic result statuses. A missing view or interrupted native observation does not determine the worker status.
- record_attempt_event is incremental and idempotent by event key. Checkpoint critical findings, decisions, blockers, and worker-attested verification claims before close. Its receipt proves identity, digest, and storage, not execution.
- `submit_attempt` accepts semantic worker facts only. Identity, timestamps, changed files, predecessor scope, manifest reconciliation, and native Stop come from canonical state; semantic verification claims retain worker provenance.
- Submission and same-attempt repair are separate tools. Each owns its complete closed one-level MCP schema; do not copy their argument fields into skills, prompts, or documentation, and do not add action branches or aliases.
- Repair is bound to the exact server-issued authority and retained draft. The returned repair contract is sufficient; never inspect private Cortex state to construct a correction.
- A successful submission does not require or return a result-reference handoff. The worker ends its task-scoped Cortex calls, and the coordinator continues 300-second generic `wait_agent` cycles for ordinary progress. The host binds the first authorized worker call to its exact native child; matching `SubagentStop` is terminal host authority. This is same-user local trust, not cryptographic or server attestation; unknown or disabled hooks fail closed.
- Worker payloads remain compact. The backend derives identity, changed paths, timestamps, manifest evidence, and native lifecycle facts; it does not claim to observe CLI, browser, console, or network execution.

## Observations and context

- Changed files are derived from the attempt baseline/current workspace observation. A worker's claimed path is not authoritative.
- Exact briefing reads and assigned predecessor-result reads create server-owned observations scoped to task, attempt, dispatch, result identity, and digest.
- Briefings are immutable capability exports, not mutable task state. Read through `read_dispatch_briefing`; only use the exact host path when that scoped read explicitly reports the file unavailable, and never locally reconstruct a receipt or digest.
- ContextCompiler is the normal coordinator-to-worker context boundary.
- HandoffCompiler is target-specific: implementation needs scope and requirements; QA needs changed files and verification; review needs the change inventory and open findings.
- Cross-stage links are server-derived and assignment-granted, never caller-invented.

## Public operations and questions

- The process audience is fixed at launch. `tools/list` is the authoritative action-specific operation inventory.
- No worker operation accepts caller-authored identity, timestamps, changed paths, predecessor receipts, or evidence markers.
- Every coordinator call preserves private task authority; every worker call preserves only exact native dispatch authority. Cortex issues that authority and callers only byte-copy it. Never infer authorization from a project scan, session/environment variable, or guessed child identity.
- The submitted worker waves have one canonical phase source validated by the MCP tool's own schema; documentation does not duplicate its field layout.
- The coordinator model, not the backend, constructs the worker waves. Cortex validates, records, and dispatches that plan but never replaces it with a server-selected pipeline.
- After a completed wave is read, use `revise_future_pipeline` only for unexecuted future work and `append_rework_wave` only for product correction of a completed canonical result. Technical failure uses the server-owned Luna-to-Terra-to-Sol replacement ladder with capability-safe profile resolution. Every returned worker repeats the native barrier, and required governance closure executes before final handoff.
- The only native lifecycle is native V2 `spawn_agent` → generic 300-second `wait_agent` cycles for ordinary progress → canonical terminal results plus exact matching `SubagentStop` events → action-specific canonical wave read and continuation. A wait has no exact-child target, and an early, timed-out, steered, partial, or unrelated ordinary wake-up requires another generic wait and authorizes no read. Native prose and wait output are never parsed for lifecycle facts. The coordinator never supplies lifecycle evidence or inspects plugin or private state. `create_thread`, `repair_planning`, and manually authored completion forms fail closed.
- Publishing a durable arbitrary-Unicode question ends the worker's native turn and genuinely pauses that child. The real answer is recorded before the same child resumes in a new native turn and polls it. Never remove and recreate a question to fix a reference mismatch.
- Interrupted native progress is resolved only through public canonical state and recovery before any new dispatch.
- A first operation pending trusted spawn observation retries only that same operation with bounded backoff until a finite deadline, without project access or replacement. A successful exact retry automatically clears the transient observer failure. At the deadline, or for another dispatch-authority failure, follow only public fail-closed recovery; do not reconstruct authority.
- A nonretryable worker final is status text, not failure authority or a result handoff. Use terminal failure finalization only when public structured recovery explicitly directs it for the original dispatch. Missing, stale, wrong-dispatch, or replayed recovery rejects without mutation; native prose is never parsed into authority.
- A first briefing page can be incomplete. Every growing read follows its exact server-issued opaque continuation with unchanged authority. Fixed receipts and atomic repair cards do not paginate. For any failure, follow only the returned structured recovery to the named action-specific operation.
- Question and answer text may use any language; no localization or structured-choice model can hold the lifecycle.

## Maintenance and governance

- Canonical writes commit before view materialization. Projection jobs are leased and verified.
- Prune commits a SQLite tombstone before filesystem cleanup.
- Governance status, immutable artifacts, exact scope, revision chains, authenticated lifecycle, and private repair escrow are server-owned in schema v19.
- Exact signed released schema-v17/schema-v18 histories upgrade transactionally in place to schema v19 and retain their append-only migration rows. The exact signed legacy V1--V8 namespace is archived privately before a fresh schema-v19 ledger is created; its task authority is not migrated or selectable. Unknown, missing, unsigned, reordered, or tampered histories fail closed and are not automatically quarantined.
- Install or update only through `./scripts/sync-cortex.sh`; Marketplace screens, direct `codex plugin` commands, and manual configuration edits are not supported alternatives.
- Documentation is navigation, not runtime evidence. Confirm consequential claims in source, schemas, executable configuration, and tests.
