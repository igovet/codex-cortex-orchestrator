# Gotchas

## Canonical state and views

- cortex.db schema v15 is authoritative. AttemptResult and append-only AttemptEvent rows are the worker protocol; JSON, Markdown, journals, and indexes are views.
- Do not repair SQLite state by editing a view. Retry the responsible projection job.
- WAL/SHM files and .state.lock are SQLite machinery. Never attach them as evidence.
- A failed serializer, view, or infrastructure operation after WORK_COMPLETED retries the same attempt. A second worker would duplicate work.

## Attempt lifecycle

- WORK_COMPLETED means the semantic result is durable; FINALIZING and COMPLETED are distinct server states.
- BLOCKED and FAILED are semantic result statuses. A missing view or interrupted native server receipt does not determine the worker status.
- record_attempt_event is incremental and idempotent by event key. Checkpoint critical findings, decisions, blockers, and verification observations before close.
- complete_attempt accepts semantic worker facts only. Identity, timestamps, changed files, predecessor scope, and verification observations come from canonical state.

## Observations and context

- Changed files are derived from the attempt baseline/current workspace observation. A worker's claimed path is not authoritative.
- Exact briefing reads and assigned predecessor-result reads create server-owned observations scoped to task, attempt, dispatch, result identity, and digest.
- Briefings are immutable capability exports, not mutable task state. Read only the exact path and digest granted by Cortex.
- ContextCompiler is the normal coordinator-to-worker context boundary.
- HandoffCompiler is target-specific: implementation needs scope and requirements; QA needs changed files and verification; review needs the change inventory and open findings.
- Cross-stage links are limited to attempt_result_ref, context_result_refs, and predecessor_result_refs.

## Public operations and questions

- The process audience is fixed at launch. The public union contains exactly nine operations.
- No worker operation accepts caller-authored identity, timestamps, changed paths, predecessor receipts, or evidence markers.
- Every task-scoped call requires the exact opaque task_ref; never infer a task by scanning a project directory.
- A worker question is durable and attempt/revision-bound. Resume the same attempt after the user answers.
- A lost native server receipt is recovered by inspecting canonical state before any new dispatch.

## Maintenance and governance

- Canonical writes commit before view materialization. Projection jobs are leased and verified.
- Prune commits a SQLite tombstone before filesystem cleanup.
- Governance status, immutable artifacts, exact scope, revision chains, and authenticated lifecycle are server-owned in schema v15.
- Documentation is navigation, not runtime evidence. Confirm consequential claims in source, schemas, executable configuration, and tests.
