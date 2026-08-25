# Conventions

<!-- GENERATED:START -->

- Machine-readable contracts live under [plugins/cortex](../../plugins/cortex/). profiles.json is the canonical profile and routing source.
- New tasks use the host-private SQLite ledger and schema v17. SQLite owns task, attempt, event, result, read observation, governance, artifact, projection, prune, and private repair-escrow state.
- Workers checkpoint semantic facts with record_attempt_event and close with complete_attempt. AttemptResult contains status, summary, findings, decisions_needed, unresolved, and optional typed claims.
- AttemptEvent rows are append-only and lossless. Stable event keys make retries idempotent.
- Cortex derives native identity, timestamps, changed paths, checks, workspace observations, and verification metadata. Caller-authored assertions cannot replace unavailable observations.
- Successful read_dispatch_briefing and assigned predecessor read_worker_result calls create idempotent server-owned read observations.
- ContextCompiler compiles complete task intent, requirements, decisions, scope, allowed paths, acceptance/verification criteria, validated predecessor result references, and server observations.
- HandoffCompiler projects only the fields needed by the target profile. Raw worker payloads are not a universal handoff.
- The immutable dispatch briefing is a private, digest-checked, identity-scoped capability artifact. Workers read it through the scoped public API; an exact host file read is allowed only when the server reports that fallback is required. It is not mutable task state.
- Result JSON/Markdown, journals, planning views, and indexes are rebuildable. They never authorize a gate, read, resume, handoff, or completion.
- The public registry has exactly nine operations: five coordinator operations and five worker operations, with read_worker_result shared by both audiences.
- `start_orchestration` is the sole task creator and initial coordinator capability issuer. Cortex owns and issues opaque refs; callers only copy and serialize them byte-for-byte. Coordinator calls carry the exact `task_ref` and `coordinator_ref`; workers carry the exact `task_ref` and `assignment_ref`, never inferred from a session or host.
- Native worker execution is only the exact server-issued `spawn_agent` target followed by exact `wait`, `read_worker_result`, and server-derived continuation. `create_thread`, session/environment authorization, server-owned CLI/executor launches, manually authored `advance`/`completions`, and `repair_planning` are not v11 contracts.
- All plan and outcome repair is a digest- and capsule-bound patch through `complete_attempt`. Lost capability fails closed; it never falls back to ambient identity or a replacement child.
- Expected domain failures use the top-level `error` and `recovery` branch only. `same_operation` requires enough returned or already-held canonical server information and explicit `allowed_changes` for a deterministic retry; `terminal_stop` uses action `none` and cannot prescribe retry, inspection, or continuation.
- Projection jobs are leased and acknowledged only after regular-file, digest, and path checks. SQLite commit and filesystem materialization are separate operations.
- WAL/SHM files and advisory locks are SQLite machinery, not records or evidence. Schema v17 is the only current task/governance boundary; private repair escrow is immutable and task-cascaded.
- Documentation is bounded by GENERATED markers. Source, tests, schemas, migrations, and executable configuration are authoritative when text drifts.

<!-- GENERATED:END -->
