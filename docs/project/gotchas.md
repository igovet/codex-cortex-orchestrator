# Gotchas

## Canonical state and views

- cortex.db schema v17 is authoritative. AttemptResult and append-only AttemptEvent rows are the worker protocol; private repair escrow retains rejected drafts, while JSON, Markdown, journals, and indexes are views.
- Do not repair SQLite state by editing a view. Retry the responsible projection job.
- WAL/SHM files and .state.lock are SQLite machinery. Never attach them as evidence.
- A failed serializer, view, or infrastructure operation after WORK_COMPLETED retries the same attempt. A second worker would duplicate work.

## Attempt lifecycle

- WORK_COMPLETED means the semantic result is durable; FINALIZING and COMPLETED are distinct server states.
- BLOCKED and FAILED are semantic result statuses. A missing view or interrupted native server receipt does not determine the worker status.
- record_attempt_event is incremental and idempotent by event key. Checkpoint critical findings, decisions, blockers, and verification observations before close.
- complete_attempt accepts semantic worker facts only. Identity, timestamps, changed files, predecessor scope, and verification observations come from canonical state.
- A successful complete_attempt does not require or return a result-reference handoff. The worker's final message is exactly `ATTEMPT_COMPLETED`; the coordinator's `read_worker_result(task_ref, coordinator_ref, step)` derives the current wave from server state.
- Worker payloads remain compact. The backend derives identity, changed paths, checks, timestamps, and evidence; do not add server-owned CLI/executor output or a manually authored completion form.

## Observations and context

- Changed files are derived from the attempt baseline/current workspace observation. A worker's claimed path is not authoritative.
- Exact briefing reads and assigned predecessor-result reads create server-owned observations scoped to task, attempt, dispatch, result identity, and digest.
- Briefings are immutable capability exports, not mutable task state. Read through `read_dispatch_briefing`; only use the exact host path when that scoped read explicitly reports the file unavailable, and never locally reconstruct a receipt or digest.
- ContextCompiler is the normal coordinator-to-worker context boundary.
- HandoffCompiler is target-specific: implementation needs scope and requirements; QA needs changed files and verification; review needs the change inventory and open findings.
- Cross-stage links are limited to attempt_result_ref, context_result_refs, and predecessor_result_refs.

## Public operations and questions

- The process audience is fixed at launch. The public union contains exactly nine operations.
- No worker operation accepts caller-authored identity, timestamps, changed paths, predecessor receipts, or evidence markers.
- Every coordinator call requires the exact `task_ref` and `coordinator_ref`; every worker call requires the exact `task_ref` and `assignment_ref`. Cortex issues refs and callers only byte-copy them. Never infer authorization from a project scan, session/environment variable, or guessed child identity.
- A start wave declares `phase` exactly once at `waves[].phase`; all workers in that wave inherit it. `waves[].workers[].phase` is unsupported. Multiple same-phase workers share a wave, while different phases use separate waves; profile overrides are constrained by the containing wave phase.
- The only native lifecycle is the exact server-issued `spawn_agent` → `wait` → `read_worker_result` → server-derived continuation route. `create_thread`, `repair_planning`, and manually authored `advance`/`completions` are legacy and fail closed.
- A worker question is durable and attempt/revision-bound. A scalar answer or stable-option selection resumes the same child; its first worker call is exactly scalar `action:"poll"` with the original refs and `question_ref`. Never remove and recreate a question to fix a ref mismatch.
- A lost native server receipt is recovered by inspecting canonical state before any new dispatch.
- Bootstrap identity repair is allowed once on the same child by byte-copying the exact server-built `bootstrap_repair_message`. If that follow-up fails, call `finalize_bootstrap_failure` for terminal cleanup; do not retry or reconstruct the message.
- Exact `CORTEX_ATTEMPT_FAILED retryable=false` is status text, not failure authority or a result handoff. Call `finalize_worker_failure` only after structured `recovery.terminal_failure.evidence="server_bound"`; Cortex verifies and consumes private current task/attempt/dispatch/generation evidence. Missing, stale, wrong-dispatch, or replayed evidence rejects without mutation.
- A first briefing page can be incomplete; use its opaque `next_cursor` with the exact worker pair until `complete=true`. For any domain failure, use only top-level `error` and `recovery`; `same_operation` needs an explicit `allowed_changes` deterministic returned contract, while `terminal_stop` has action `none` and never authorizes retry or inspection.

## Maintenance and governance

- Canonical writes commit before view materialization. Projection jobs are leased and verified.
- Prune commits a SQLite tombstone before filesystem cleanup.
- Governance status, immutable artifacts, exact scope, revision chains, authenticated lifecycle, and private repair escrow are server-owned in schema v17.
- Documentation is navigation, not runtime evidence. Confirm consequential claims in source, schemas, executable configuration, and tests.
