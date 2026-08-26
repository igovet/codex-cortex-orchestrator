# Security policy

## Scope

This repository contains the Cortex 11.0.1 Codex plugin. The runtime is
opt-in, runs locally, and stores orchestration state in a host-private SQLite
v19 ledger. The supported public contract is the v11 task and capability
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
- native V2 `spawn_agent` and generic timeout-bounded `wait_agent` cycles as
  the only worker lifecycle;
- private identity joining through host MCP `_meta.threadId` matched to
  `SubagentStart.agent_id`, with matching `SubagentStop` as the prerequisite for backend
  worker-wave reads and continuation;
  session/environment variables are never authorization;
- immutable dispatch briefings scoped to the exact task and attempt;
- immutable coordinator-authored acceptance and verification criteria, bound
  by one digest through assignment, result evaluation, closure, and handoff;
- SQLite AttemptEvent rows and one canonical AttemptResult per attempt;
- server-observed timestamps, native identity, exact native Stop, changed
  files, workspace state, and manifest reconciliation;
- explicitly worker-attested command, browser, console, network,
  accessibility, layout, and test claims whose receipts attest only exact
  identity, digest, and storage;
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
After a completed wave is read, the coordinator makes one evidence-frontier
decision. `revise_future_pipeline` changes only unexecuted future waves;
`append_rework_wave` appends product correction and independent verification
for a completed canonical result. Neither operation is technical recovery.
Transport, host-observation, model, and lifecycle failures use the server-owned
exact-occurrence Luna-to-Terra-to-Sol replacement ladder with capability-safe
profile resolution. Every newly returned worker repeats the native lifecycle,
and required governance closure executes before final handoff.

Every public response follows its closed v11 contract. Worker briefing,
question, event, completion, and result responses remain minimal. A successful
completion is terminal and does not expose a worker-carried result reference.
Backend worker-wave reads and continuation remain unavailable until every bound
child has a canonical terminal result and exact matching terminal `SubagentStop`.
Ordinary generic wait output is progress only and never lifecycle
evidence. The coordinator never supplies lifecycle evidence or inspects plugin
or private state. Error and
recovery responses provide the bounded structured data required for the
advertised deterministic next operation without exposing private state.
Expected validation and domain
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
timestamps, task revision, profile, phase, changed paths, workspace manifests,
canonical result identity, and exact native Stop. Execution checks remain
worker-attested; a storage receipt never converts them to a server observation.
Missing required evidence remains explicit and fails closed.

Worker requests contain compact semantic fields only. Identity, changed paths,
timestamps, workspace observations, manifest evidence, and native lifecycle
facts are backend-derived; semantic execution claims are worker-attested.
Capabilities are preserved only in bounded, digest-checked
handoffs. A missing or lost capability fails closed; Cortex never falls back
to `CORTEX_WORKER_BINDING_JSON`, `CODEX_SESSION_ID`, `CODEX_THREAD_ID`, or any
other ambient session/environment value.

Submitted-report repair uses the dedicated `repair_attempt` operation and is
digest- and capsule-bound to the same attempt. A server-built bootstrap repair,
distinct from pending spawn observation, may be applied once to the same native
child; if that repair fails, terminal bootstrap cleanup follows the public
recovery route.
A worker whose first operation is still awaiting trusted spawn observation
retries only that same operation with bounded backoff until a finite deadline.
It makes no project access, switches no operation, and never spawns a
replacement. A successful exact retry automatically clears the transient
observer failure; at the deadline it follows public fail-closed recovery.
A nonretryable worker final is status only and never authorizes failure. Use
terminal worker-failure finalization only when public structured recovery
explicitly directs that action for the original native dispatch. Cortex
verifies and consumes its private current binding before terminalizing the
assignment; missing, stale, wrong-dispatch, expired, or replayed recovery
rejects without domain mutation. Arbitrary child prose is never parsed into
failure authority or stored as the reason.
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

Fresh state uses one compact schema-v19 ledger. Exact signed released
schema-v17 and schema-v18 histories are authorized predecessors and upgrade
transactionally in place, preserving their append-only migration rows before
the schema-v19 row is appended. The exact signed legacy V1--V8 namespace is
archived privately before a fresh schema-v19 ledger is created; its task
authority is neither migrated nor selectable. An incomplete, unsigned,
reordered, tampered, symlinked, non-regular, unsafe, or otherwise unknown
database fails closed and is not automatically quarantined, guessed, or adopted.

Backups and pruning must be SQLite-aware. A tombstone is committed before
removing a view. Never use a broad recursive deletion command for Cortex state,
and never copy a database between users or unrelated projects.

## Lifecycle and hooks

The hook observations are trusted local input inside the same-user local-state boundary,
not cryptographic proof or server attestation. Malicious same-user modification
of the plugin or its private database is outside the supported threat model.
Unknown hook identity, disabled execution, missing trust, or unverifiable hook
state fails closed: worker-wave reads and continuation remain unavailable.

The registered native hooks observe native lifecycle events without turning host identity
into capability authority. Host MCP thread metadata plus trusted
`SubagentStart`/`SubagentStop` privately join a worker to its dispatch and record
the exact terminal Stop. `SubagentStop` is the terminal host authority.
Ordinary 300-second wait cycles are progress only. The hooks do not infer a task
from ambient session data, recover a stopped child, or accept prose or ordinary
wait output as lifecycle evidence.

For live-proof review, Cortex retains only a bounded private host-metadata audit
of lifecycle event classes, equality outcomes, and nonreversible digests. It
never stores raw host identities, payloads, messages, transcripts, paths,
reports, or capabilities in that audit. The audit is neither public nor model-
visible and grants no lifecycle or recovery authority.

Review hook commands before trust. They must invoke the installed cache's
bundled scripts/cortex-launcher and scripts/cortex_hook.py, and the content
hashes must match the selected plugin. Start a new Codex task after
installation or update. The sole supported installation/update command is
`./scripts/sync-cortex.sh`; Marketplace and direct `codex plugin` commands are
not alternative trust or installation paths.

## Prompt and plugin integrity

Prompt Contract v3 remains the sole stable prompt path. Dispatch-controlled
values are fenced assignment data; static policy remains in the bundled skill
and profile sources. The standalone marketplace/runtime publishability test is
the sole release gate. Prompt lint, deterministic prompt evaluation,
marketplace validation, compilation, and diff review are supporting diagnostics,
not separate test suites or release gates. The release label remains 11.0.1 and
the ledger schema remains v19. Prompt Contract v3 and the independent plain-text
question contract retain their own schema histories; neither is a public
task-protocol version.

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

1. Run the sole standalone marketplace/runtime publishability test documented
   in `docs/release-readiness.md`. There is no focused or full source suite.
2. Use prompt lint/evaluation, marketplace validation, Python compilation,
   `git diff --check`, and Markdown link and command review only as supporting
   diagnostics; do not report them as additional tests or release gates.
3. Verify manifest version, action-specific MCP contract parity, and schema
   v19 against the exact working tree.
4. Verify that no private data, credentials, temporary state, or generated
   cache files enter the package.
5. State any unavailable host-installation or live-model checks explicitly.

<!-- END SECURITY POLICY -->
