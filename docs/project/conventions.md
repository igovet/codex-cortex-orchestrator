# Conventions

<!-- GENERATED:START -->

- Machine-readable contracts live under [plugins/cortex](../../plugins/cortex/). profiles.json is the canonical profile and routing source.
- New tasks use the host-private SQLite ledger and schema v18. SQLite owns task, attempt, event, result, read observation, governance, artifact, projection, prune, and private repair-escrow state.
- Workers checkpoint semantic facts with `record_attempt_event`, submit with `submit_attempt`, and apply a server-issued same-attempt correction only through `repair_attempt`.
- Every semantic action has its own MCP tool. Each tool owns a complete closed one-level `inputSchema` from `public_contracts.py`, and runtime validation uses the same schema. Tool descriptions are short semantics; skills and prompts contain no argument fields or schema templates. There are no aliases or multiplexed action branches.
- AttemptEvent rows are append-only and lossless. Stable event keys make retries idempotent.
- Cortex derives native identity, timestamps, changed paths, checks, workspace observations, and verification metadata. Caller-authored assertions cannot replace unavailable observations.
- Successful `read_dispatch_briefing` and assigned `read_predecessor_result` calls create idempotent server-owned read observations.
- ContextCompiler compiles complete task intent, requirements, decisions, scope, allowed paths, acceptance/verification criteria, validated predecessor result references, and server observations.
- HandoffCompiler projects only the fields needed by the target profile. Raw worker payloads are not a universal handoff.
- The immutable dispatch briefing is a private, digest-checked, identity-scoped capability artifact. Workers read it through the scoped public API; an exact host file read is allowed only when the server reports that fallback is required. It is not mutable task state.
- Result JSON/Markdown, journals, planning views, and indexes are rebuildable. They never authorize a gate, read, resume, handoff, or completion.
- `tools/list` is the authoritative public operation inventory; prose does not maintain a duplicate registry.
- `start_orchestration` is the sole task creator and initial coordinator capability issuer. Cortex owns and issues opaque authority; callers only copy and serialize it byte-for-byte. Coordinators retain private task authority, while workers retain only exact native dispatch authority, never inferred from a session or host.
- The coordinating model owns construction of the submitted worker waves. The backend validates them, records the plan, and issues exact dispatches; it does not choose a replacement pipeline or reconstruct model-owned workers. Semantic text is language-neutral: no locale or language can block a plan, question, answer, event, or report.
- Native worker execution is only the exact server-issued `spawn_agent` target followed by exact `wait`, an action-specific canonical wave read, and server-derived continuation. `create_thread`, session/environment authorization, server-owned CLI/executor launches, manually authored completion forms, and `repair_planning` are not v11 contracts.
- Submitted-report repair is digest- and capsule-bound through `repair_attempt`. Lost capability fails closed; it never falls back to ambient identity or a replacement child.
- Every growing public read uses a server-issued opaque `c11p` cursor. Fixed receipts and atomic repair cards do not paginate.
- Expected failures return structured recovery sufficient to select the next action-specific operation without source, cache, log, ledger, session, or environment inspection.
- Projection jobs are leased and acknowledged only after regular-file, digest, and path checks. SQLite commit and filesystem materialization are separate operations.
- WAL/SHM files and advisory locks are SQLite machinery, not records or evidence. Schema v18 is the only current task/governance boundary; private repair escrow is immutable and task-cascaded. Only the exact signed V11 v1--v8 database lineage may upgrade atomically to v18. Its former task authority is retained only as private, non-selectable migration state; unknown, missing, reordered, or unsigned histories fail closed.
- The only supported source-checkout install or update path is `./scripts/sync-cortex.sh`; do not substitute marketplace installation, direct plugin commands, or configuration edits.
- Documentation is bounded by GENERATED markers. Source, tests, schemas, migrations, and executable configuration are authoritative when text drifts.

<!-- GENERATED:END -->
