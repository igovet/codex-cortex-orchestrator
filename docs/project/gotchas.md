# Gotchas

## Canonical state and views

- cortex.db schema v18 is authoritative. AttemptResult and append-only AttemptEvent rows are the worker protocol; private repair escrow retains rejected drafts, while JSON, Markdown, journals, and indexes are views.
- Do not repair SQLite state by editing a view. Retry the responsible projection job.
- WAL/SHM files and .state.lock are SQLite machinery. Never attach them as evidence.
- A failed serializer, view, or infrastructure operation after WORK_COMPLETED retries the same attempt. A second worker would duplicate work.

## Attempt lifecycle

- WORK_COMPLETED means the semantic result is durable; FINALIZING and COMPLETED are distinct server states.
- BLOCKED and FAILED are semantic result statuses. A missing view or interrupted native server receipt does not determine the worker status.
- record_attempt_event is incremental and idempotent by event key. Checkpoint critical findings, decisions, blockers, and verification observations before close.
- `submit_attempt` accepts semantic worker facts only. Identity, timestamps, changed files, predecessor scope, and verification observations come from canonical state.
- Submission and same-attempt repair are separate tools. Each owns its complete closed one-level MCP schema; do not copy their argument fields into skills, prompts, or documentation, and do not add action branches or aliases.
- Repair is bound to the exact server-issued authority and retained draft. The returned repair contract is sufficient; never inspect private Cortex state to construct a correction.
- A successful submission does not require or return a result-reference handoff. The worker's final message is exactly `ATTEMPT_COMPLETED`; the coordinator reads the current wave through its private authority and the server derives it from canonical state.
- Worker payloads remain compact. The backend derives identity, changed paths, checks, timestamps, and evidence; do not add server-owned CLI/executor output or a manually authored completion form.

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
- The only native lifecycle is the exact server-issued `spawn_agent` → exact `wait` → action-specific canonical wave read → server-derived continuation route. `create_thread`, `repair_planning`, and manually authored completion forms fail closed.
- A worker question is durable and attempt/revision-bound. Questions and answers are arbitrary-Unicode text, not structured-choice or localized records. The worker LLM interprets adequacy after polling the answer. Never remove and recreate a question to fix a reference mismatch.
- A lost native server receipt is recovered by inspecting canonical state before any new dispatch.
- Dispatch-authority recovery is allowed only on the exact server-returned same-child route. If recovery fails, follow the returned terminal cleanup; do not retry or reconstruct authority.
- Exact `CORTEX_ATTEMPT_FAILED retryable=false` is status text, not failure authority or a result handoff. Call `finalize_worker_failure` only after structured `recovery.terminal_failure.evidence="server_bound"`; Cortex verifies and consumes private current task/attempt/dispatch/generation evidence. Missing, stale, wrong-dispatch, or replayed evidence rejects without mutation.
- A first briefing page can be incomplete. Every growing read follows its exact server-issued opaque `c11p` cursor with unchanged authority. Fixed receipts and atomic repair cards do not paginate. For any failure, follow only the returned structured recovery to the named action-specific operation.
- Question and answer text may use any language; no localization or structured-choice model can hold the lifecycle.

## Maintenance and governance

- Canonical writes commit before view materialization. Projection jobs are leased and verified.
- Prune commits a SQLite tombstone before filesystem cleanup.
- Governance status, immutable artifacts, exact scope, revision chains, authenticated lifecycle, and private repair escrow are server-owned in schema v18.
- Only the exact signed V11 v1--v8 database lineage upgrades atomically to v18. Prior task authority is retained only as private, non-selectable migration state; unknown, missing, unsigned, or reordered history fails closed. Do not substitute quarantine/fresh-ledger behavior for that rule.
- Install or update only through `./scripts/sync-cortex.sh`; Marketplace screens, direct `codex plugin` commands, and manual configuration edits are not supported alternatives.
- Documentation is navigation, not runtime evidence. Confirm consequential claims in source, schemas, executable configuration, and tests.
