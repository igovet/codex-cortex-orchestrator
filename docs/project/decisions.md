# Decisions

## Ordered waves with parallel attempts

The ledger keeps an ordered wave sequence because it makes dependencies and
completion decisions auditable. Independent gates and multiple agents for one
gate may run concurrently inside the active wave; every passed attempt must
produce linked evidence before the wave advances. Dependent work belongs in a
later wave. A general DAG would be a separate schema decision rather than an
implicit reinterpretation of the current state model.

## Audience-projected nine-operation public facade

The v5 registry exposes nine operations. An unspecified or unknown launch-time
audience uses a compatibility projection exposing all nine, so the ordinary
Desktop route `$cortex:orchestrator` can start orchestration. Explicit
`worker` and `coordinator` audiences project exactly five tools. Coordinators receive
`start_orchestration`, `continue_orchestration`, `manage_orchestration`,
coordinator-only `manage_governance`, and scoped `read_worker_report`; workers
receive `worker_question`, `get_report_template`, `record_report`,
`read_dispatch_briefing`, and scoped `read_worker_report`. The shared report
reader remains handler-scoped: coordinators read completed reports, while
successor workers may read only explicitly supplied predecessor refs.
A coordinator starts a task with
the compact task contract, then continues once per completed wave.
The active-wave cursor is a relative `step`; parallel results use only
relative worker slots. Start owns classification, ledger initialization,
full-plan persistence, and first-wave preparation. Every worker persists its
exact seven-field `cortex/report/v1` through one atomic `record_report` after
building from `get_report_template`. `get_report_template` creates a fully
structured private JSON draft file with mode `0600` and returns `draft_ref`,
`draft_path`, and expiry without returning its body. Writers edit that exact
file; read-only workers may submit a small RFC 7396 merge patch or complete
replacement through `record_report`. Invalid records leave the same file in
place and consume no attempt. A new template supersedes an old or expired
draft. `record_report` receives worker identity and `draft_ref`, validates the
current file and state, and deletes the file and metadata only after an atomic
commit. A legacy full payload remains compatible. The worker returns only
`REPORT_RECORDED report_ref=<value>` plus at most a two-sentence summary (or
the exact report-tool error), and never sends the report body in its native
final. The coordinator reads each ref, then Continue validates all parallel
refs before state writes, records evidence and gates, and returns the next
step. Interrupted native acknowledgement is recoverable because inspect lists
persisted `available_reports`.

## Ordinary-chat post-plan approval

Required approval is a state-machine hold after the singleton final Planner
wave, not a prose convention. The pending review is bound to the plan revision,
planner report, verified predecessor digest, semantic future-pipeline digest,
and an opaque request ID. Cortex returns a declarative
`cortex/chat-interaction/v1` containing the understandable plan summary,
approve/revise/cancel meanings, and the LLM-recommended response with its
rationale. The coordinator renders it as one ordinary final assistant message,
calls no UI/input/approval/elicitation tool, and ends the turn. The next user
message must carry the current interaction ID when recorded; stale, replayed,
or mismatched submissions fail closed. Approve advances the next wave, Revise
preserves feedback verbatim and reruns Planner, whereas Cancel records no
dispatch and keeps `awaiting_user` pending. Silence cannot advance the task.

## Server-owned governance records

`manage_governance` persists bounded initiatives and typed dependency edges,
append-only scoped records with content digests and superseding revisions,
active snapshots, constrained exceptions, and coordinator-approved promotion
with project-scoped proposals and policy. Workers and reviewers may publish
proposals, but cannot activate or approve policy. Coordinator governance
authorization is one-response capability material with only a durable digest;
`off` requires an exhaustive structured assessment, sensitive records enforce
policy retention and access controls at write time, and independent close
review is bound to canonical reviewer attempts and sessions. Full governance
adds `governance_activation` and `governance_close` review waves owned by
`code_reviewer`; the resolver never invents a numeric scope trigger.

Governance schema v12 makes the integrity boundary explicit. A record is read
from and verified against its immutable content artifact; the legacy
`content_json` column is only a checked cache. Every record has a normalized,
non-null scope key, task/initiative links are checked exactly, and a partial
unique index permits at most one successor for a predecessor. Immutable-field
triggers reject direct SQL mutation, strict JSON rejects non-finite values, and
`submission_id` plus a command digest makes retries conflict-safe. Schema v12
adds an append-only cryptographic lifecycle chain for status and approval
basis, prevents deletion of governance-scoped initiative-task links, and
requires terminal success for linked milestone/deliverable tasks before an
initiative completes. A pre-v10 upgrade reconciles only deterministic v9
duplicate revisions/sibling successors and fails closed on ambiguous graphs.
Promotion and its proposal transition must share one SQLite transaction. A
coordinator capability carries task/initiative scope, principal, thread,
generation, expiry, allowed actions, and revocation metadata; recovery is
available on the normal compatibility or explicit coordinator audience and
requires the same active identity plus a non-durable recovery proof. It stages
one HMAC-derived replacement pair that can be redelivered after a lost recovery
response; acknowledgement requires the prior proof and both replacements before
the old generation is revoked. A lost initial start response remains
fail-closed without a host-attested delivery identity. An explicit worker
audience cannot invoke either phase and plaintext values are never stored.

## Chunked immutable artifact transport

Large coordination evidence is stored as immutable SQLite artifacts, not as a
large JSON field returned from MCP. Schema v8 adds task/plan revisions, native
worker sessions, attempt messages, trace/tool observations, and question-batch
storage; schema v7 separates a deduplicated content
blob, a task-scoped logical artifact, and an authorized export path. Each blob
has a content SHA-256, MIME type, exact byte size, and 32 KiB `TEXT` or `BLOB`
chunks; the database schema does not impose a small per-field storage cap.
Logical-artifact metadata is indexed by task/kind/time and task/time, while
exports are path-authorized separately. Public transport is
separate from storage: `manage_orchestration(intent="artifacts")` pages
metadata and reads an artifact part, while existing briefing/report tools use
the same bounded mechanism for their established scopes. Signed opaque cursors
bind reader, task, artifact, digest, and byte offset, so they cannot be reused
to enumerate a ledger or switch content. Markdown and JSON task files remain
materialized audit/Desktop projections; normal reads and report validation use
the database copy. A missing or damaged projection may be regenerated from its
canonical SQLite artifact through a leased outbox job, but pre-SQLite
coordination files are never scanned, imported, or repaired by the active
ledger.

## SQLite commits and filesystem projections

SQLite is the sole atomic boundary. Canonical content, logical-artifact
metadata, export authorization, and a projection job commit before a filesystem
worker claims a lease. That worker atomically materializes one private regular
file, verifies its digest, then acknowledges the job in a separate transaction.
This makes failures retryable without treating a filesystem write as part of
the original SQLite transaction. Required dispatch briefings are an exception
only in capability semantics: the exact private export/digest must exist before
the native worker starts, while the canonical content remains SQLite-owned.

Review and close decisions derive from canonical structured findings and
server-observed verification. Open P0/P1 findings, explicitly blocking open
findings, and missing required verification reopen the recorded gate for
rework. P2 is advisory unless its authoritative finding sets `blocking=true`.
Resolved findings and non-self auditable waivers remain durable records; prose
cannot silently close them.

The separate worker question operation exists because a native parent-channel
message alone cannot enforce a pause. Every profile may persist a material
question, return its compact ref, remain alive, and poll the answer on the same
attempt. Report intake and continuation fail closed while a blocking question
is open. This preserves dialogue without treating Planner as a special case or
forcing a replacement worker after every user response.

Answered intent is task-scoped even though question resumption is
attempt-scoped. The report service automatically snapshots every canonical
answered question, answer, selected option ID, source ref, and digest into the
top-level `resolved_user_decisions` sibling and report Markdown. Replacement
briefings receive a bounded recent projection, while predecessor reports retain
the complete list. Successors must not ask a materially equivalent question
under new wording or keys unless the current user explicitly changes the
decision. Choice-based batch forms always expose optional `custom_response`;
localized custom text follows the same canonical-English translation barrier
as ordinary free text.

Questions are bound to the task revision, plan revision, and attempt strategy
generation. A material steer supersedes unresolved questions and invalidates
downstream evidence; an answer from an older revision is rejected rather than
applied to the new pipeline. Question quotas are scoped to the active revision
or generation, so a long-running corrective task can ask a new blocking
question after a user-approved strategy change without losing the audit trail.

The coordinator is the sole pipeline authority: it builds or consciously
accepts the initial waves, follows the returned pipeline snapshot by default,
and changes `future_waves` only when verified evidence materially changes
ownership, dependencies, risk, sequencing, or validation. Planner and explorer
recommendations are advisory, and every replacement carries the coordinator's
concise reason. The public facade derives rework intent when a supplied future
pipeline repeats a current or completed gate, so a missing boolean cannot turn
an otherwise valid recovery into a caller-correction loop. The internal engine
still records an explicit audited rework transition.
The lifetime `replan_count` is audit history only. The legacy `replan_limit`
field remains readable for old tasks but never blocks a new evidence-backed
corrective cycle. Per-gate failure counts and strategy names are escalation and
audit evidence only: corrective cycles remain unbounded while acceptance or
findings require work. Future-wave approval and rework requirements are
preflighted before gate recording, and Planner-first resume may repair an
active task only when its current wave has no live or pending dispatch.
Semantically unchanged future-wave reassessment records an unchanged receipt
and continues, while v5 future waves are internally renumbered so public
relative steps never move backward or collide.

The open-ended correction policy has a liveness boundary rather than a fixed
attempt quota. Cortex computes a no-progress signature from the finding
fingerprint, affected paths, dispatch/manifest digest, verification result,
and failure class; free-text reason prose is audit-only. Repeated materially
identical signatures pause autonomous work in a user-decision state, preserving
the failed gate and evidence instead of producing a false pass. A new
Planner-first recovery generation must materially change the pipeline,
strategy, or verification contract, unless the Planner wave names a
class-matched infrastructure/environment remediation.

A final close report may itself reveal bounded work that invalidates terminal
proof. When the coordinator supplies an explicit rework `future_waves`
replacement in that same continuation, Cortex reopens the pipeline even though
the close transition has already marked it completed internally. The replaced
gate and every downstream attempt, evidence record, and report receipt are
invalidated before new dispatches are returned. This is distinct from a
later user correction of an already returned completed task, which still uses
a new linked follow-up task.

The final advance reconciles reports and the project manifest, records the
documentation decision, verifies close evidence observed by the server,
creates the handoff and audit record, and completes the task. `inspect`,
`resume`, `deactivate`, `lane`, `resource`, and `question` are recovery or
nested operations of the same facade. Legacy v7 primitives and task data are
unsupported and are neither migrated nor resumed. This keeps the public
lifecycle coupled only to the canonical v8 ledger.

Project cleanup is a bounded `prune` management operation rather than clear.
It requires explicit confirmation and an age threshold (seven days by default).
First it commits a tombstone while retaining the canonical task graph; after
safe filesystem projection removal outside the state lock, one final SQLite
transaction removes completed task-scoped state and unreferenced secondary
records. Failures retain recoverable task state and a retryable tombstone.
Active and blocked tasks are preserved regardless of age, and classification
receipts remain while any retained task references them. Recent completed
tasks, durable lanes, and all project/plugin content are also preserved. Exact
`PRUNE` confirmation and omission of `task_ref` keep this as project-scoped
maintenance rather than task mutation.

Each mutating request uses server-owned digest receipts. Identical retries
replay safely; changed payloads and stale steps conflict before partial writes.
This is deliberately per project root: every call carries an absolute
`project_root`, and one server process can serve multiple roots. Internal task,
wave, attempt, report, evidence, and receipt IDs remain durable for audit but
are not normal-flow input.

The public exception is the opaque `task_ref` returned by Cortex. The
coordinator preserves it on every later lifecycle and report-read call so
different task contracts can run concurrently below one project root without
cross-session ambiguity. A byte-identical active start replays the same task;
the replay returns no native dispatches and therefore cannot launch a duplicate
wave. If the original **successful** response was lost before dispatch,
management inspect recovers only still-awaiting requests using that exact ref.
Changed task or wave content creates a distinct task. An omitted ref always
returns `task_ref_required` rather than relying on a process-wide "active
task." A failed start without a ref created no task to recover.

Human-readable complexity, phase, profile, status, and common language aliases
are normalized before task-state creation. This keeps the public schema small
without pushing fragile enums or BCP-47 spelling repairs onto a Luna parent;
unknown phase/profile values still fail before ledger writes with bounded
suggestions. In particular, `implement` normalizes to `implementation` and
`build_verification` to the final `close` phase, preventing retry loops caused
by treating common phase labels as new work.

## Modular executable facade

`plugins/cortex/scripts/cortex.py` remains the stable executable, hook-import,
and public facade. Its runtime responsibilities are deliberately split
into focused bundled modules: identity formatting, route policy, delegation
persistence, immutable dispatch briefings, scoped reports and questions,
gate-transition policy, orchestration state transitions, harvest validation,
context-compaction handoff rendering, health/legacy maintenance, and the public
MCP schema/transport adapter. The record-report vertical slice is separated
into domain policy, ports, a SQLite unit-of-work adapter, projection port, and
use case; `core/runtime_bindings.py` is the explicit composition binding, not a
bidirectional facade import. The public adapter owns the declarative
nine-operation registry and JSON-RPC stdio loop; the facade passes the current
business handlers into it. Gate transitions are further separated into active-
gate resolution, evidence/C2-C3 validation, durable state mutation, and
terminal manifest cleanup, so routing changes cannot accidentally weaken
completion policy.

The facade exports documented public symbols so installed hooks and tests do
not depend on a module's physical source location. Tests therefore assert
behavior and exported contracts rather than `def` placement. The import bridge
also supports Codex's installer validation, which loads the entrypoint through
`importlib` without first registering a module name. It neither reads nor
migrates pre-SQLite task state, which remains unsupported under the v8 policy.

## Explicit activation and layered worker prompts

`cortex:orchestrator` is an explicit opt-in route. Its frontmatter and the
bundled internal skills make that boundary visible: ordinary task complexity
does not activate Cortex. Once selected, `cortex-control` is the single
runtime-core overlay for coordinator state, dispatch, recovery, and private
diagnostics; the orchestrator skill supplies route and role guidance without
duplicating the runtime protocol.

Worker Briefing v3 has one artifact-backed assignment envelope. Task-controlled values are
JSON-serialized as untrusted `Assignment data`, so headings, fences, markup,
and instruction-like text remain data rather than prompt structure. The
briefing no longer repeats model/effort routing metadata, prompt-baseline
references, or a second copy of the exact user request. Role playbooks remain
profile-owned, while harvest-specific guidance is a conditional
`mode_overlays.harvest` entry in `profiles.json`, not duplicated in every
profile TOML.

Prompt compaction keeps non-blocking targets in the worker contract: 1,500
bytes for the native bootstrap, 16 KiB for ordinary briefings, and 18 KiB for
harvest briefings. They guide template design only; no dispatch or Planner
report is rejected for crossing a prompt-size threshold. Complete plans remain
immutable artifacts and implementation briefings carry only their exact
digest-bound references. The marketplace validator
and regression tests also lint long duplicate prompt paragraphs and parse
adversarial assignment data as JSON.

Rework policy is explicit, evidence-driven, and unbounded. QA, review,
implementation, and corrective phases repeat while acceptance, verification,
or canonical findings remain unresolved. Counts are durable audit/routing
inputs rather than budgets. The first unresolved cycle floors effort at
`high`, the second at `xhigh` and eligible work at Terra, and the third and
later cycles at `max`. `next_strategy` and replanning are optional when new
evidence supports them; neither is a prerequisite for continuing correction.

## Conditional indexed repository intelligence

Codebase Memory is an optional worker-side accelerator, not a source of truth
and never a root-coordinator inspection path. Cortex mirrors upstream
`cbm_project_name_from_path` and places the canonical-root-derived project key
in every worker briefing. Workers query that key directly; `list_projects` is
limited to one fallback after direct not-found, ambiguity, or apparent
drift/collision, and its result must match the exact canonical root. Graph,
architecture, and trace operations are
preferred for initial discovery and impact analysis, but consequential facts
must be confirmed in current source and tests. If the service, matching index,
or result is unavailable or stale, `planner`, `explorer`, `architect`, and
`database_architect` may perform one bounded refresh; other profiles fall back
to ordinary repository tools after one failed attempt. No profile loops on
setup or recovery. This preserves useful indexed context without weakening
source authority or the coordination-only root lock.

## Explicit predecessor handoffs

Verified worker handoffs are executable context, not optional prose. Omitted
`depends_on` selects all verified predecessors, an explicit phase list selects
only those prerequisites, and an empty list declares intentional independence.
Before dispatch, Cortex reduces that selection to its transitive DAG frontier:
a passed report covers only the exact refs that its attempt read and
acknowledged. The generated prompt requires the worker to reconcile every
frontier handoff against current evidence and add the exact generated
`Predecessor review:` marker to report evidence. Public `record_report` rejects
missing acknowledgements. Covered reports remain immutable in SQLite and in
the final Planner evidence digest; they are not discarded or silently omitted.
There is no task-wide report-count, report-aggregate-byte, or separate
predecessor-count quota. Individual reports, attempts, briefings, and storage
integrity remain bounded. Compact inspect/recovery views independently retain
eight recent summaries.

## Repository knowledge is routed context, not authority

When available, `docs/project/index.md` and `docs/features/index.md` are
automatically added to every worker briefing. The planner selects the linked
pages relevant to the task and recommends their exact paths; the coordinator
attaches that selection to future workers through `context_files`. All workers
re-check the indexes and record `Knowledge reviewed:` evidence naming every
available index and additional page used. Public report intake rejects a
missing index acknowledgement. Explicit context paths must be existing
project-relative regular files and cannot be absolute, traversing, missing, or
symlinked. Documentation remains navigation and prior knowledge: consequential
claims are verified against current source, tests, schemas, migrations, or
executable configuration.

## Exhaustive knowledge harvest

Repository knowledge is maintained as a source-backed feature census rather
than a recent-change summary. `docs/features/index.md` is the coverage manifest,
and incremental harvest is allowed only after it proves a zero-gap baseline.
Otherwise Cortex runs Planner Scope, domain-partitioned discovery, architecture
synthesis, documentation, independent completeness review, and close. A large
repository uses 2–8 bounded explorers and non-overlapping documentation owners.
Completion requires behavior-complete feature pages, evidence-backed
exclusions, zero unexplained unmapped surfaces, and—for refresh—a no-change
second documentation plan. Harvest documentation, review, and close also
validate the feature index structurally; a shallow link list without Coverage
matrix columns, Inventory totals, Unmapped surfaces, Exclusions, or Known
unknowns cannot satisfy the coverage-manifest contract.

Harvest routes retain the internal planning phase but force `plan_approval` to
`auto`; they never introduce a post-plan user approval hold. User tasks keep
the normal C1/C2/C3 approval policy.

## Explicit maintenance, legacy lifecycle, and health

The active SQLite ledger never imports legacy filesystem coordination state.
The separate, explicit legacy lifecycle can inventory it, create a verified
private archive, and delete only the archived sources after an archive-specific
confirmation. Health inspection is read-only; checkpoint, private
`.cortex-backup` disaster-recovery bundle creation, fresh-host restore
verification, optimize, vacuum, and projection reconciliation each require an
action-specific confirmation. A published bundle atomically contains
`cortex.db`, the separately host-stored governance lifecycle HMAC key, and a
manifest binding their fingerprints; verification restores both into a fresh
disposable host layout and validates governance records through the real v12
authority layer. Historical bare `.sqlite` snapshots are not represented as
recoverable Cortex backups. WAL/SHM are SQLite sidecars, not exports, backups,
evidence, or independent prune targets. Lifecycle telemetry and model metrics
are not canonical task artifacts and must not be used as completion proof.

## Bounded private commit-adapter recovery

Composite gate commits must not turn adapter mistakes into an active task that
can be retried forever. A unique context grant may be normalized to its
attempt-bound report receipt because the server can verify task, gate, attempt,
and one-use ownership. Any other repeated `commit_gate` validation failure for
the same gate/mode is persisted as a recovery event; after three failures the task is
blocked with a handoff/resume action. This preserves the exact failure for
repair while guaranteeing a terminal control-plane outcome. This safety cap is
strictly local to the private atomic adapter; it does not limit pipeline, QA,
review, worker, finding-remediation, or closure rework cycles.

## Same-user host readiness and hook trust

Host readiness is intentionally stricter than “the source directory exists” or
“the cache contains a plugin.” The read-only `cortex-host-preflight.py`
diagnostic emits seven prerequisite checks: `codex_cli`, `cortex_python`,
`plugin_root`, `codex_home`, `cortex_registration`, `cortex_mcp_config`, and
`cortex_hook_trust`. `mcp.status=READY` requires every check to pass. The
registration check requires exactly one enabled, installed `cortex@cortex`
entry at the checked version for the same Codex user; the MCP check requires a
regular, non-symlink `config.toml` with Cortex enabled and
`default_tools_approval_mode = "approve"`; hook
trust requires all five enabled, trusted, cache-backed hooks and matching
persisted hashes. The script
does not install or mutate anything, so remote provisioning remains an
operator-authorized step and an unavailable approved runtime is reported as a
blocker. This closes the false-positive class where source or cache evidence
looked healthy while the active SSH user’s registration or hook binding was
missing.

## Terminal recovery for reportless native stops

A native worker stopped without a durable report or question is a terminal
failed attempt, recorded as
`native_worker_stopped_without_report`. It is not an active or resumable child:
the handoff exposes its exact `dispatch_ref`, and the coordinator submits one
failed continuation with that ref and reason. Cortex alone may return a fresh
top-level dispatch; the dead child is never waited on, respawned, or resumed
with `followup_task`. A stopped worker whose lifecycle is
`report_recorded` is completion-pending, not active and not resumable, even
when `host_report_refs` contains reports. The coordinator must explicitly
continue with exactly one chosen `report_ref`; Cortex verifies that ref against
the exact stopped `attempt_id`, gate, task, and current revision before
consuming it. Cortex never auto-selects a report, silently approves it, or
respawns the stopped child. Missing, stale, already-consumed, or mismatched
refs fail closed and require the normal recovery path; multiple valid refs
remain audit-visible until the coordinator chooses one. A worker paused on a durable
question remains resumable through its exact host identity. Repeated failed
continuations remain eligible for fresh top-level
dispatches while Cortex raises the effort floor (`high`, then `xhigh`, then
`max`) and uses Terra for eligible ordinary work after two prior failures.
Only an explicit non-retryable blocker or user cancellation stops correction.
This separates evidence-bearing and user-paused stops from the no-report
deadlock while preserving idempotent, identity-scoped recovery.

The corresponding PostToolUse recovery is deliberately ordering-independent:
it searches all matching reportless attempts in the current gate, so a later
completed retry cannot hide an earlier failed attempt whose exact failed
receipt is still needed.

## C2/C3 proof requirements

C2 and C3 tasks require delegation-linked evidence and a final handoff before
completion. The v8 contract additionally requires a consumed classification receipt,
a status observation before delegation, with stale revision/receipt hints safely corrected by the serialized server, a `technical_writer` documentation
decision, an explicit reassessment decision, server-observed successful command
evidence at close, a complete project-manifest receipt, and an attempt-tied
single-use worker-report receipt for delegated evidence. C1 remains lightweight
and may use direct evidence for local work.

The C2/C3 close worker report is independently checked before gate evidence is
accepted: it must contain an executed test or verification result, observed
evidence, no unresolved completion markers, and a textual mapping from every
task-level acceptance and verification criterion to that evidence. A summary
that merely asserts completion cannot close the task.

Executed-check gates use a stronger report invariant: every `report.tests`
entry in a successful completion report has integer `exit_code: 0`. Negative
paths must be wrapped in an assertion harness that treats the expected failure
as a successful assertion and exits 0. Any observed nonzero result is retained
as failure evidence, rejects report publication as
`worker_verification_failed`, and triggers repair or coordinator-authorized
rework; a different passing entry cannot cancel it.

Reworking a gate invalidates that gate and every downstream gate, including
their prior evidence, so a later pass cannot accidentally reuse stale proof.

## Manifest-backed handoffs and bounded host correlation

Each v8 task captures a project file manifest at initialization and compares it
at reconciliation or handoff. A final handoff must name every detected changed
file, including additions, deletions, modifications, and recognized renames.
This makes touched-file reporting checkable without relying on a worker's
summary.
The initial and each per-attempt capture are stored as immutable,
content-addressed records in the host-private `cortex.db` at
`~/.codex/cortex/projects/p-<sha256>/` by default. State and attempt records carry
compact `manifest-<sha256>` references; repeated captures of the same project
state deduplicate to the same record, while a fresh capture at every dispatch
preserves detection of external changes. Once terminal completion is persisted,
the manifest records are removed because the final receipts already retain
digest and change proof. An
explicit `allow_rework` transition from completed to active establishes a fresh
initial baseline before replacement dispatches.

### SQLite migration contract

`cortex.db` is the sole mutable source of truth for new tasks and is host-private
by default below `~/.codex/cortex/projects/p-<sha256>/`. The plugin keeps
numbered, content-checked migrations through v12 in `ledger_db.py`; the first
MCP call with a new migration takes the project-ledger lock, applies every
missing migration in order inside one SQLite transaction, and records each
version in `schema_migrations`. Repeated calls verify history and schema.
A mismatched checksum or failed migration aborts without mutating the database.
Pre-SQLite filesystem state is never read, imported, deleted, or resumed by the
active ledger. Future releases add a new numbered SQLite migration rather than
introducing a second file-backed runtime.
The manifest scope is policy-driven: Cortex honors each applicable project
`.gitignore`, including ordered negations, and freezes the discovered rules in
the baseline policy for the lifetime of the task. It also excludes
high-confidence dependency, cache, test-output, and runtime directories across
languages. Ambiguous output names (`build`, `dist`, `target`, `bin`, and `obj`)
are excluded only when an ignore rule or recognizable build marker confirms
that they are generated output; a source directory with one of those names is
not hidden by name alone. This balances complete changed-file accounting with
the practical need to avoid inventorying virtual environments and package
caches.

Manifest capture also has explicit `max_entries`, `max_hashed_bytes`, and
`max_seconds` budgets. A budget breach produces a partial manifest with a
reason and never masquerades as a complete baseline, mutation-audit basis,
handoff, or terminal-close proof. A bounded in-process
digest cache reuses unchanged file hashes only when the full stat identity
matches. The release workflow runs the 50,000-file benchmark and requires its
`target_met` result before the CI job can pass.

The ledger stores `principal` and `thread_id` for authorization and a
delegation attempt for auditability, but it deliberately labels the link as
`ledger_attempt_only`: the local server has no trustworthy host-side spawn
attestation. Hooks therefore record sanitized lifecycle context and canonical
agent-name guidance only. State mutations are lock-serialized and each JSON
replacement is fsync-backed; related task, lane, index, and journal writes are
not a single crash-atomic transaction. These limits keep the guarantees
explicit rather than overstating local-file durability.

The confirmed host child/thread id is additionally indexed as a narrow alias
for that attempt's worker report. It is never accepted as a coordinator
principal and cannot authorize task, gate, evidence, or pipeline mutations.

## Optional execution lanes

The task ledger remains the default orchestration surface. A lane is an
optional durable execution container for persistent or multi-repository work.
It owns a lease, portable declarations, and cross-task resource claims, but it
may materialize only explicitly declared Git worktrees under a live lease and
explicit confirmation. It never uses force removal, refuses dirty retirement,
and does not launch arbitrary processes. This keeps C1 work lightweight while
providing Mandate-style recovery and collision control for C3 work.

## Explicit activation

The Cortex control plane is inactive by default. In Desktop, select
`cortex:orchestrator` through the Skills picker or mention
`$cortex:orchestrator`; in CLI, lead with `$cortex:orchestrator` or use
`/skills` and select it. Non-help, non-`normal` skill routes authorize the
main/root agent to activate the server-side mode. The server's `/cortex` and
`/normal` values are internal protocol tokens only; the host does not register
bare native slash commands for them. Use `$cortex:orchestrator normal` to leave
the route, and never present either bare token as a required user recovery
step. Classification, task creation, delegation, gates, lanes, and claims
cannot mutate state before activation.

## Documented v5 lifecycle identity

New v5 tasks keep their generated authorization identity immutable. A
synchronous `PostToolUse` hook binds the returned opaque `task_ref` to the
documented hook `session_id` in a separate private registry, so identities
already embedded in a dispatch response remain valid. `SessionStart` uses that
binding after `resume`, `clear`, or `compact`. Environment values are only a
compatibility fallback, and model-visible context is emitted under
`hookSpecificOutput.additionalContext`.

Plugin installation and reload remain operator-owned. A fresh Codex thread is
required after installation or update so the new hook, skill, and MCP paths are
loaded.

The installer also owns lifecycle-hook trust. During an explicit sync it calls
Codex `hooks/list` for the project and accepts exactly the five enabled
`cortex@cortex` hooks, requiring each `sourcePath` to be the installed
`hooks/hooks.json`, each command to invoke the installed `cortex_hook.py`, and
each `currentHash` to have the complete `sha256:` form. Those hashes are stored
in the global hook-state configuration; `sync-cortex.sh --check` performs the
same read-only validation. This makes changed or untrusted `PreToolUse` and
`PostToolUse` definitions a visible installation failure instead of a silent
host-binding regression. It does not replace the fresh-thread requirement.

Native worker identity follows the same separation: `profile` remains
canonical, while `display_name` is derived from the task domain in the user's
request (for example, `Planner Authentication`), without a number or digest.
Gate mission verbs are not used as the display module. `spawn_agent.task_name` is its
lower-underscore equivalent with a deterministic uniqueness digest; it uniquely
names the task/attempt session and satisfies the host's `[a-z0-9_]{1,80}` name
contract. Hyphens are normalized only for this native field and the digest
prevents collisions; durable Cortex IDs retain their hyphen-compatible format.
Cortex rejects reuse of a `host_agent_id` already
bound to another attempt. Only `followup_task` for the exact confirmed native
worker may resume it. Dynamic `SubagentStart` events expose
`agent_type=default`, so lifecycle hooks use the exact returned dispatch
identity to map each opaque child ID back to the issued native task key and
canonical profile for worker-context injection.

## Visible-thread checkout selection

Visible `create_thread` dispatches are user-owned tasks created by the host,
not hidden `spawn_agent` workers. Cortex records the selected profile and
model in the request and supplies the profile instructions in the generated
prompt. Their checkout is explicit: `thread_environment` defaults to `local`
so a read-only visible task stays in the saved project, while callers can
request `worktree` for concurrent or write-heavy work. The coordinator maps
the value to the native `target.environment.type`; local sharing is a
deliberate trade-off and requires serializing writers.

## Host-private runtime state

Production orchestration is fail-closed for each supplied absolute
`project_root`; one MCP server process can serve multiple roots while storing
coordination state below the host-private project hash root. The v5 public tools
validate the selected root before preparing work, and an unavailable server or
failed, unwritable, or mismatched root ends that task's workflow with a blocker.
Ordinary/unledgered subagent work is not a substitute.

## Skill-level Cortex routes

The supported native entry is the host-discovered `cortex:orchestrator` skill
(or `$cortex:orchestrator` prompt reference). Cortex subcommands are deterministic skill
arguments, not separately registered slash commands. An empty argument selects
ordinary task orchestration; `help`, `harvest`, `harvest-refresh`, and `normal`
select the other routes. Help is read-only; normal deactivates session state
without creating a task. Knowledge routes retain the v8 task, delegation,
gate, project-manifest, verification, and handoff contracts.

## Bundled profile contract and capability-aware routing

`plugins/cortex/profiles.json` is the single machine-readable source for the 21
supported profile names, sandbox modes, automatic gate routes, and the shared
worker report contract. The removed `task_formatter` profile is not accepted by
the server. Model selection remains a coordinator dispatch decision within the
machine-validated adaptive policy in the same contract, and Cortex persists
requested, selected, policy, and fallback fields. `explorer` always selects
Luna, with coordinator-selected effort or a risk-based default; Terra is only
its host-unavailable fallback. Security context, the security gate, and
`security_auditor` always select Sol with complexity floors C1 `medium`, C2
`high`, and C3 `xhigh`. Ordinary profiles are classified as efficient,
adaptive, or deep. Efficient work uses Luna; deep profiles and
`terra_task_kinds` entries use Terra. C2/C3 planning and those entries
(including uncertain
diagnosis, long-context, and integration-conflict work), plus high/critical
failure cost, also use Terra; other low/moderate-risk adaptive work stays on
Luna. Efficient Luna uses
C1/C2/C3 `high`/`high`/`xhigh`; bounded adaptive Luna uses
`high`/`xhigh`/`max`; Terra uses `high`/`high`/`xhigh`, all subject to the risk
floor. The accepted effort vocabulary ends at `max`; automatic `max` covers
C3 adaptive Luna work and repeated unresolved corrective failures. Coordinator Luna/Terra overrides remain
available but cannot lower the computed effort floor.
Non-security Sol is valid only for an explicit user model request represented
by matching `user_requested_model` and `requested_model`; old
`sol_escalation`, auditable-extreme, failed-Terra, and model/effort-remap
authorization is removed. The coordinator passes the exact
`spawn_agent` catalog and, after a fresh install, the confirmed
`spawn_agent_default_model`. A Luna route prefers that configured default,
then an explicit Luna override, and finally an explicit hidden Terra override.
The hidden Terra fallback preserves selected effort. Automatic visible-thread
fallback is not part of model routing.

## Scoped worker report bus

Workers build a strict seven-field `cortex/report/v1` payload from the private
JSON file created by `get_report_template`, correct it in place, and publish
the file once through atomic
`record_report`; the v8 report primitive stores the canonical
sanitized JSON record, which is task- and attempt-bound; server-owned receipts
make retries idempotent. A receipt links one report to one C2/C3
evidence record and is consumed once. Its `reports/consumptions/` tombstone is
irreversible and prevents replay even if reconciliation repairs derived files.
The task index exposes metadata only.
Delegation indexes separate reports owned by an attempt from report bodies
explicitly granted as context. This keeps cross-worker context intentional and
bounded while acknowledging that local principal/thread values are
caller-asserted, not host identity attestation.
