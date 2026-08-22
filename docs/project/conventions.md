# Conventions

<!-- GENERATED:START -->

- Machine-readable contracts live under [plugins/cortex](../../plugins/cortex/). profiles.json is the canonical profile and routing source.
- New tasks use the host-private SQLite ledger and schema v15. SQLite owns task, attempt, event, result, read observation, governance, artifact, projection, and prune state.
- Workers checkpoint semantic facts with record_attempt_event and close with complete_attempt. AttemptResult contains status, summary, findings, decisions_needed, unresolved, and optional typed claims.
- AttemptEvent rows are append-only and bounded. Stable event keys make retries idempotent.
- Cortex derives native identity, timestamps, changed paths, checks, workspace observations, and verification metadata. Caller-authored assertions cannot replace unavailable observations.
- Successful read_dispatch_briefing and assigned predecessor read_worker_result calls create idempotent server-owned read observations.
- ContextCompiler compiles bounded task intent, requirements, decisions, scope, allowed paths, acceptance/verification criteria, validated predecessor result references, and server observations.
- HandoffCompiler projects only the fields needed by the target profile. Raw worker payloads are not a universal handoff.
- The immutable dispatch briefing is a private, digest-checked, identity-scoped capability file. It is required for dispatch and is not mutable task state.
- Result JSON/Markdown, journals, planning views, and indexes are rebuildable. They never authorize a gate, read, resume, handoff, or completion.
- The public registry has exactly nine operations: five coordinator operations and five worker operations, with read_worker_result shared by both audiences.
- Coordinator calls carry the exact opaque task_ref. Workers cannot call lifecycle or management operations.
- Projection jobs are leased and acknowledged only after regular-file, digest, and path checks. SQLite commit and filesystem materialization are separate operations.
- WAL/SHM files and advisory locks are SQLite machinery, not records or evidence. Schema v15 is the only current task/governance boundary.
- Documentation is bounded by GENERATED markers. Source, tests, schemas, migrations, and executable configuration are authoritative when text drifts.

<!-- GENERATED:END -->
