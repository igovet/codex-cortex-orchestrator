# Security policy

## Scope

This repository contains the Cortex 11.0.1 Codex plugin. The runtime is
opt-in, runs locally, and stores orchestration state in a host-private SQLite
v18 ledger. The supported public contract is the v11 task and capability
protocol.

## Supported security boundary

Cortex treats the following as authoritative:

- server-issued opaque task, coordinator, assignment, dispatch, and attempt
  identities;
- `start_orchestration` as the sole task creator and initial coordinator
  capability issuer;
- coordinator calls carrying exact private task authority, and worker calls
  carrying only their exact native dispatch authority;
- Cortex as the sole issuer of those opaque refs; models only byte-copy and
  serialize issued values, never derive them from host or session state;
- native `spawn_agent` and exact `wait` targets as the only worker lifecycle;
  session/environment variables are never authorization;
- immutable dispatch briefings scoped to the exact task and attempt;
- SQLite AttemptEvent rows and one canonical AttemptResult per attempt;
- server-observed timestamps, native identity, changed files, checks,
  workspace state, and verification observations;
- target-specific ContextCompiler and HandoffCompiler projections;
- server-derived result references and assignment-granted predecessor context
  for cross-stage handoff.

Generated JSON, Markdown, journals, plans, and indexes are views. Their text,
presence, links, or filenames cannot authorize a read, gate, resume, handoff,
or completion.

The public facade uses action-specific MCP tools. Lifecycle, inspection,
recovery, user interaction, approval, follow-up, steering, artifacts, lanes,
resources, governance, attempt submission and repair, briefings, wave reads,
and predecessor reads are distinct operations. There are no multiplexed action
selectors or compatibility aliases. The MCP `tools/list` response is the
authoritative operation inventory.

The coordinator model authors the start plan; the backend validates, records,
and dispatches it. Every public operation advertises its own complete, closed,
one-level `inputSchema`, built by the canonical public-contract module. Runtime
validation consumes the same schema object. Tool descriptions state short
operation semantics; skills, prompts, and documentation contain no copied
argument fields, schema templates, or alternate spellings. Questions and
answers are arbitrary-Unicode text, not structured choices or localized forms.

Every public response follows its closed v11 contract. Worker briefing,
question, event, completion, and result responses remain minimal. A
successful completion is terminal and does not expose a worker-carried result reference;
the worker's final message is exactly `ATTEMPT_COMPLETED`. Coordinator result
reads derive the current wave from canonical server state rather than accepting
a child-carried result reference. Error and recovery responses provide the
bounded structured data required for the advertised deterministic next
operation without exposing private state. Expected validation and domain
failures are MCP tool-execution-error results (`isError=true`) with concise
sanitized text and the operation's closed structured response when the
transport preserves it. Callers use only that public contract and never inspect
source, cache, logs, ledger, session, environment, or hidden paths.
Recovery never mutates canonical task state unless the invoked action-specific
tool is explicitly a mutation. Cortex may retain immutable private repair
escrow needed for same-attempt correction; that row is not public evidence.

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

Submitted-report repair uses the dedicated `repair_attempt` operation and is
digest- and capsule-bound to the same attempt. Bootstrap repair may be applied once to the same native
child by byte-copying the server-built `bootstrap_repair_message` unchanged;
if that repair fails, `finalize_bootstrap_failure` performs terminal cleanup.
The exact child marker `CORTEX_ATTEMPT_FAILED retryable=false` is status only
and never authorizes failure. `finalize_worker_failure` accepts the original
native dispatch authority and fixed sanitized reason only after a structured
`recovery.terminal_failure.evidence="server_bound"` action backed by current,
unexpired private task/attempt/dispatch/generation evidence. It verifies and
consumes that evidence atomically before terminalizing the exact assignment;
missing, stale, wrong-dispatch, expired, or replayed evidence rejects without
domain mutation. Expired control evidence is removed by bounded exact-key
cleanup. Arbitrary child prose is never stored as the reason.
An issued completion repair is immutable for its active attempt. Caller-
correctable failures reissue the same repair authority; integrity tampering
remains terminal. Repair cards are self-contained, so workers never inspect
source, schemas, logs, or ledger state to invent values. Only
capability/identity tampering, base-integrity mismatch, or terminal-attempt
authorization failure is nonretryable.
Repair cards are atomic and do not paginate. Every growing public read is
server-paged by an opaque `c11p` cursor; fixed receipts do not paginate.
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

Only the exact signed V11 v1--v8 database lineage is accepted as migration
input. Cortex validates the complete lineage and upgrades it atomically to
schema v18. Any historical task authority retained during that operation is
private, non-selectable migration state: it cannot be chosen, inferred, or
reused through a public task reference. Missing, unsigned, reordered,
tampered, symlinked, non-regular, unsafe, or otherwise unknown histories fail
closed; Cortex neither guesses a predecessor nor reinterprets it as a fresh
ledger.

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
installation or update. The sole supported installation/update command is
`./scripts/sync-cortex.sh`; Marketplace and direct `codex plugin` commands are
not alternative trust or installation paths.

## Prompt and plugin integrity

Prompt Contract v3 remains the sole stable prompt path. Dispatch-controlled values
are fenced assignment data; static policy remains in the bundled skill and
profile sources. Prompt lint, deterministic prompt evaluation, marketplace
validation, and source-mode package checks are required before release. The
release label remains 11.0.1 and the ledger schema remains v18. Prompt Contract
v3 and the independent plain-text question contract are retained as their own
schema histories, not treated as public task-protocol versions.

Live prompt checks are optional development evidence performed in an ordinary
interactive Codex CLI or tmux session. They must not launch nested evaluator
processes, carry task material into another runtime, or be represented as
worker-lifecycle or release-gate evidence.

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
   version, action-specific MCP contract parity, and schema v18.
5. Verify that no private data, credentials, temporary state, or generated
   cache files enter the package.
6. State any unavailable host-installation or live-model checks explicitly.

<!-- END SECURITY POLICY -->
