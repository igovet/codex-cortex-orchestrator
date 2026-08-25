# Security policy

## Scope

This repository contains the Cortex 11.0.1 Codex plugin. The runtime is
opt-in, runs locally, and stores orchestration state in a host-private SQLite
v17 ledger. The supported public contract is the v11 task and capability
protocol.

## Supported security boundary

Cortex treats the following as authoritative:

- server-issued opaque task, coordinator, assignment, dispatch, and attempt
  identities;
- `start_orchestration` as the sole task creator and initial coordinator
  capability issuer;
- coordinator calls carrying the exact `task_ref` and `coordinator_ref`, and
  worker calls carrying the exact `task_ref` and `assignment_ref`;
- Cortex as the sole issuer of those opaque refs; models only byte-copy and
  serialize issued values, never derive them from host or session state;
- native `spawn_agent` and exact `wait` targets as the only worker lifecycle;
  session/environment variables are never authorization;
- immutable dispatch briefings scoped to the exact task and attempt;
- SQLite AttemptEvent rows and one canonical AttemptResult per attempt;
- server-observed timestamps, native identity, changed files, checks,
  workspace state, and verification observations;
- target-specific ContextCompiler and HandoffCompiler projections;
- attempt_result_ref, context_result_refs, and predecessor_result_refs for
  cross-stage result links.

Generated JSON, Markdown, journals, plans, and indexes are views. Their text,
presence, links, or filenames cannot authorize a read, gate, resume, handoff,
or completion.

The public operation set is exactly nine. Coordinator calls are
start_orchestration, continue_orchestration, manage_orchestration,
manage_governance, and read_worker_result. Worker calls are worker_question,
record_attempt_event, complete_attempt, read_dispatch_briefing, and
read_worker_result.

Every public response is a closed v11 union. Lifecycle responses expose a
typed action and only the route-specific dispatch, wait, question, approval,
handoff, or top-level `error` + `recovery` branch. Governance exposes a typed receipt or
an explicit-inspect typed inspection. Worker briefing, question, event,
completion, and result responses carry only their minimal canonical fields. A
successful completion is terminal and does not expose an `attempt_result_ref`;
the worker's final message is exactly `ATTEMPT_COMPLETED`. Coordinator result
reads use the exact `task_ref`/`coordinator_ref`/`step` tuple and derive the
current wave from canonical server state, rather than accepting a child-carried
result reference.
Generic `user_message`, `user_view`, `internal`, full pipeline/governance
state, and prose `next_action` fields are not public output. Error and recovery
responses retain patch-critical diagnostics, original JSON Pointer paths,
exact semantic repair pointers, bounded nested field schemas, signed opaque
repair handles, base payload digests, and allowed patch paths; state mutation
is explicitly false for those branches. Expected validation and domain
failures are MCP tool-execution-error results (`isError=true`) with concise
sanitized text plus the Cortex `ok=false`, `error`, and `recovery` structure
when the transport preserves structured content. They are not raw JSON-RPC
argument errors. Callers use only that public contract and never inspect
source, cache, logs, ledger, session, environment, or hidden paths.
`same_operation` is valid only when the returned response or an already-held
canonical server contract provides explicit `allowed_changes` and makes a legal
retry deterministic. A terminal
`recovery.kind=terminal_stop` has action `none`, never a contradictory retry,
inspection, or continuation action.

For these branches, `state_mutated=false` means no canonical task, revision,
attempt, result, event, workspace, or project mutation. Cortex may create or
reuse one immutable private repair-escrow row needed for same-attempt repair;
that row is not public evidence and exposes neither the rejected draft nor the
raw bearer.

## Data handling

Do not place credentials, access tokens, personal data, private task content,
or raw host/model responses in prompts, issues, commits, generated views, or
logs. Canonical task, result, event, question, answer, briefing, and artifact
content is stored losslessly; prompt compactness targets are advisory only.
Structural identity, strict-JSON, cursor, pagination, lifecycle, and private
diagnostic-retention guards remain in force, and the runtime rejects symlink
and non-regular targets at capability boundaries.

The worker contributes semantic facts only. The server derives identity,
timestamps, task revision, profile, phase, changed paths, executed checks,
workspace observations, and verification metadata. Missing observations remain
explicit and cannot be converted into a successful verification claim.

Worker requests contain compact semantic fields only. Identity, changed paths,
timestamps, checks, workspace observations, and verification metadata are
backend-derived. Capabilities are preserved only in bounded, digest-checked
handoffs. A missing or lost capability fails closed; Cortex never falls back
to `CORTEX_WORKER_BINDING_JSON`, `CODEX_SESSION_ID`, `CODEX_THREAD_ID`, or any
other ambient session/environment value.

Planning and outcome repair use only a digest- and capsule-bound patch through
`complete_attempt`. Bootstrap repair may be applied once to the same native
child by byte-copying the server-built `bootstrap_repair_message` unchanged;
if that repair fails, `finalize_bootstrap_failure` performs terminal cleanup.
After an already-started child returns exact
`CORTEX_ATTEMPT_FAILED retryable=false`, `finalize_worker_failure` accepts only
the original structured `dispatch_ref` and fixed sanitized reason code. It
terminalizes that exact current assignment atomically, keeps its briefing
receipt, events, and repair escrow, and creates no result or replacement.
Arbitrary child prose is never stored as the reason.
An issued completion repair is immutable for its active attempt: full draft
replay and caller-correctable patch errors return the same handle, digest, and
diagnostic path scope. A malformed model copy of the handle is caller-correctable
and reissues that same repair; a structurally valid handle with a failed MAC is
tampering and remains terminal. Repair cards must be self-contained, so workers
never inspect source, schemas, logs, or ledger state to invent values. Only
capability/identity tampering, base-integrity mismatch, or terminal-attempt
authorization failure is nonretryable.
`repair_planning`, server-owned CLI/executor launches,
`create_thread`, and manually authored `advance`/`completions` payloads are
not supported v11 surfaces.

Read observations are idempotent and scoped to the exact task, attempt,
dispatch, result identity, and digest. A read from one task cannot satisfy
another task's requirement.

## State and filesystem safety

The default state root is a private, host-owned directory under
~/.codex/cortex/projects/p-<sha256>/. An explicit
CORTEX_HOST_STATE_DIR override must be private, outside the workspace, and
owned by the current user. Broad roots such as /, /home, /tmp, or a repository
root are not valid state roots.

SQLite is the atomic boundary. WAL/SHM files, advisory locks, and projection
jobs are coordination machinery, not application evidence. Filesystem views are
regular, private, digest-checked, and rebuildable. A failed view or serializer
after WORK_COMPLETED retries finalization on the same attempt; it never
creates a second worker for the same work.

When a new task opens a host ledger whose migration history or schema is not
the exact current canonical record, Cortex privately quarantines the complete
old namespace as one archive (SQLite database and sidecars, task/lane files,
coordination files, and the lifecycle key) and starts a fresh schema-v17
ledger only when the namespace is the exact canonical v16 predecessor. That
v16 namespace is quarantined as a unit and no row, migration, task, lane,
coordination file, sidecar, or lifecycle key is adopted into v17. It never
migrates or imports that state, and this recovery is not returned as a
user-visible form error. V15 and older, unknown, tampered, symlinked,
non-regular, or unsafe filesystem targets remain fail-closed and are not
quarantined automatically.

Backups and pruning must be SQLite-aware. A tombstone is committed before
removing a view. Never use a broad recursive deletion command for Cortex state,
and never copy a database between users or unrelated projects.

## Lifecycle and hooks

The five hooks observe native spawn/wait lifecycle events without binding them
to capability authority. They do not authorize a worker from ambient session
data, infer a task by scanning directories, recover a stopped child, or accept
prose as proof. An active dispatch without a finalized canonical result cannot
authorize a gate, handoff, terminal completion, or coordinator stop. A host
child binding is private telemetry, not authorization and not proof that work
completed; the coordinator follows only the server-returned public lifecycle
branch for its exact capability pair.

Review hook commands before trust. They must invoke the installed cache's
bundled scripts/cortex-launcher and scripts/cortex_hook.py, and the content
hashes must match the selected plugin. Start a new Codex task after
installation or update.

## Prompt and plugin integrity

Prompt Contract v3 remains the sole stable prompt path. Dispatch-controlled values
are fenced assignment data; static policy remains in the bundled skill and
profile sources. Prompt lint, deterministic prompt evaluation, marketplace
validation, and source-mode package checks are required before release. The
release label is 11.0.1 and the ledger schema remains v17. Prompt Contract v3
and independent question-schema versions are retained as their own schema
histories, not treated as public task-protocol versions.

## Vulnerability reporting

Please do not include credentials, personal data, private task contents, or
raw diagnostic logs in a public issue. If a confidential reporting channel is
listed for the published repository, use it. Otherwise open a minimal public
issue containing only a safe description and a way to request a private
conversation.

When reporting, include the affected version, operating system, reproduction
steps that contain no secrets, expected behavior, observed behavior, and
whether the issue affects the plugin package, MCP server, lifecycle hook, or
host-state boundary. Do not attach a database, prompt transcript, cache, or
worker output.

Maintainers should acknowledge a report, reproduce it in an isolated project,
assign severity, coordinate remediation, and publish a new version with
verified package hashes. Keep the test case deterministic and remove personal
or operational data before committing it.

## Release safety checklist

Before publishing 11.0.1 or a later release:

1. Run the focused protocol, lifecycle, context, handoff, governance, and
   packaging tests.
2. Run the full source test suite and record exact counts.
3. Run prompt lint/evaluation and marketplace validation.
4. Run git diff --check, inspect links and commands, and verify manifest
   version, public operation count, and schema v17.
5. Verify that no private data, credentials, temporary state, or generated
   cache files enter the package.
6. State any unavailable host-installation or live-model checks explicitly.

<!-- END SECURITY POLICY -->
