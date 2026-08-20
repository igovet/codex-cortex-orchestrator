# Orchestration ledger, report bus, and lane lifecycle

<!-- GENERATED:START -->
## Purpose

The local MCP server implements the Cortex 9.2.11 `cortex/v8` task ledger plus
the additive v12 governance ledger and public `cortex/orchestration/v5`
lifecycle, staged waves, worker questions/reports, maintenance, governance,
and optional execution lanes through a nine-operation v5 registry. Each
launch-time audience exposes exactly five tools: the coordinator projection
contains `start_orchestration`, `continue_orchestration`,
`manage_orchestration`, coordinator-only `manage_governance`, and scoped
`read_worker_report`; the worker projection contains `worker_question`,
`get_report_template`, `record_report`, exact identity/digest-scoped immutable
briefing fallback `read_dispatch_briefing`, and scoped `read_worker_report`.
Pre-SQLite ledgers and facades are unsupported and must be recreated. Cortex
never imports or resumes filesystem coordination state. Future SQLite schema
migrations run automatically and exactly once per project database in an
explicit atomic transaction. Each migration is recorded with a content-based
SHA-256 checksum over its version, name, and ordered normalized SQL. Legacy
name-only checksums are accepted only after schema validation and are upgraded
to the content checksum; inconsistent history fails closed.

## Key files and dependencies

- [cortex.py](../../../plugins/cortex/scripts/cortex.py) is the stable executable and public facade for task, report, and lane tools.
- [gate_transitions.py](../../../plugins/cortex/scripts/cortex_runtime/gate_transitions.py) owns active-gate resolution, evidence policy, C2/C3 completion requirements, durable transitions, and terminal manifest cleanup behind the `record_gate` facade.
- [orchestration_engine.py](../../../plugins/cortex/scripts/cortex_runtime/orchestration_engine.py) owns orchestration start/continue/inspect transitions, transaction checkpoints, waves, and native dispatch assembly.
- [ledger_db.py](../../../plugins/cortex/scripts/cortex_runtime/ledger_db.py) owns the SQLite schema, content-checked migration history through v12, governance tables, blobs, logical artifacts, export authorization, projection jobs, prune tombstones, revision/session/question-batch tables, and signed artifact cursors without importing the MCP entrypoint.
- [governance.py](../../../plugins/cortex/scripts/cortex_runtime/governance.py) owns mode resolution, initiative/dependency integrity, immutable records and snapshots, constrained exceptions, and coordinator-approved promotion.
- [projection_service.py](../../../plugins/cortex/scripts/cortex_runtime/projection_service.py) owns leased outbox materialization and retry; [health_maintenance.py](../../../plugins/cortex/scripts/cortex_runtime/health_maintenance.py) owns explicit SQLite-aware health, backup, and projection-reconciliation maintenance.
- [harvest_validation.py](../../../plugins/cortex/scripts/cortex_runtime/harvest_validation.py) owns exhaustive harvest coverage-manifest checks.
- [profiles.json](../../../plugins/cortex/profiles.json) is the canonical machine-validated source for all 21 profiles, their descriptions, sandboxes, route categories, gates, selection/avoidance guidance, adaptive model/effort routing, ordered implementation routing, scope/plan gate briefings, conditional harvest mode overlays, and the `cortex/report/v1` field contract.
- [test_cortex_control.py](../../../tests/test_cortex_control.py) covers report-bus scoping/reconciliation and lane lifecycle behavior.

Repository-root `AGENTS.md` is development-only and is not installed. Runtime
guarantees come exclusively from the installable `plugins/cortex/` tree; the
bundled orchestrator is explicit opt-in and hands the runtime protocol to
`cortex-control`, the single runtime core for root isolation, dispatch,
ownership, verification, recovery, and private diagnostics.

## Canonical artifact layout

The root ledger owns the host-private `cortex.db` (mode `0600`) and advisory
`.state.lock` below the default `~/.codex/cortex/projects/p-<sha256>/` root.
`CORTEX_HOST_STATE_DIR` is a host-only override and must be private and outside
the workspace. SQLite is the sole mutable source for tasks, state, plans,
operations, report/delegation indexes, questions, snapshots, classifications,
lanes, activations, resource claims, findings, projection jobs, prune
tombstones, revisions, worker sessions, attempt messages, trace observations,
and immutable artifact content. Schema v11 added append-only governance lifecycle
authority for status and approval basis, governed link-deletion restrictions;
schema v12 adds host-keyed authentication for the complete lifecycle envelope
and terminal linked-task checks. Schema v10 adds governance integrity indexes,
scope/revision triggers, and idempotent submission receipts; schema v9 added
the initial governance tables and records;
schema v8 added revision-aware task/plan
records, native worker-session identity, attempt messages, trace/tool
observations, and atomic question-batch storage exposed through
`ask_batch`/`poll_batch`. Schema v7 separates three durable
identities: a digest-addressed content blob with 32 KiB verified chunks, a
task-scoped logical artifact (kind/title/immutability), and an authorized
filesystem export path. Historic v2 artifact tables remain migration evidence;
new reads and writes use the normalized model. A task directory holds only
lazy, materialized human-facing projections—exact briefings, report
Markdown/JSON, planning revisions, handoffs, and journals—rather than another
source of truth.

Question answers are stored with their attempt for exact pause/resume, but
their authority is projected task-wide. Every new report artifact receives an
automatic `resolved_user_decisions` sibling and Markdown section with the
canonical questions and answers, selected option IDs, source refs, and
digests. Successor briefings require review of that ledger before another
question is published. Choice-based batch steps always render optional
free-form input and preserve its original and canonical-English forms.

Large bodies are never embedded in a lifecycle response. `manage_orchestration`
with `intent="artifacts"` lists bounded metadata pages and reads one selected
artifact in a server-limited UTF-8/BLOB part. `read_dispatch_briefing` and
`read_worker_report` use the same bounded chunk transport for their existing
scopes. Each opaque HMAC cursor is bound to the task, artifact ref, digest,
reader audience, and byte offset; a caller must resend the full identity tuple
and may advance only with the returned cursor. The server materializes a
missing report Markdown projection from the canonical artifact before issuing
its Desktop link. An export is authorized and placed in the SQLite outbox
before a materializer claims a lease, atomically writes and fsyncs it, verifies
the digest, and acknowledges it. Requested `max_bytes` values above 32768 are
normalized to the transport bound for briefing, report, and coordinator
artifact reads, so caller mistakes do not become SQLite export failures. Full
content is only assembled internally for state-machine validation, where every
chunk and full digest is verified.

Hook/tool observations are deduplicated by task, attempt, context epoch, and
normalized fingerprint in the v8 ledger. A successful full-coverage
observation is reusable for the same workspace generation; repeats increment
the duplicate count without replacing the successful observation. A later
success may supersede a failed observation, while partial coverage never
authorizes reuse.

The runtime deliberately does not create `v3-operations.json`, active-task or
status-receipt files, `reports/grants`, `metrics.json`, task lock files,
handoff-manifest snapshots, evidence-snapshot files, or new
`baseline-manifest.json`/per-attempt baseline files. Pre-SQLite task files are
intentionally ignored: they are not imported, resumed, altered, or deleted by
Cortex.
New task and attempt records instead carry compact `manifest-<sha256>` refs to
immutable content-addressed database records. Equal project state shares one record, but each
dispatch performs a fresh capture so external changes remain visible.

The default host-private state root is created only under secure `0700` parent
directories with no symlink ancestry. A legacy project-local `.codex/cortex`
database is moved only by a same-filesystem atomic rename after database and
split-state checks; unsafe, non-database, or cross-filesystem legacy state
fails closed rather than being copied or resumed in place.

## Behavior and status

The stdio MCP process has one immutable launch-time audience. An unspecified
or unknown audience uses the compatibility projection exposing all nine public
operations, so `$cortex:orchestrator` works in an ordinary Desktop launch.
Explicit `worker` and `coordinator` audiences remain strict five-tool
projections. JSON-RPC initialization and tool arguments cannot change the
audience or elevate a worker process.

`start_orchestration` accepts an absolute `project_root` and requires the
user's exact, unexpanded text in `task.user_request`. Desktop's sole
host-metadata exception is its injected
`$cortex:orchestrator`
wrapper, which Cortex canonicalizes to `$cortex:orchestrator` before task
identity, labels, persistence, and worker prompts. The route and every
following user-authored word are preserved; arbitrary Markdown links and user
paths remain unchanged. This prevents local plugin-cache paths and
cache-version changes from entering durable task state. This is the current
intent boundary. When `task.objective` is present, it must match the trimmed
`user_request` exactly; paraphrase or coordinator expansion is rejected before
ledger writes. Cortex
defaults complexity to safe C2, builds the standard pipeline when waves are
omitted, and prepares the first wave. Each
`continue_orchestration` call supplies the relative active-wave `step`, the
opaque `task_ref` returned by a successful lifecycle response, and persisted worker `report_ref` values. A single-worker
completion may omit its slot; a parallel completion uses short relative
`worker: 1..N` slots, while a non-success completion also carries the exact
`dispatch_ref` for its attempt. The server validates completeness,
uniqueness, and ownership atomically before state writes, then returns the
next step and native dispatch arguments. Future-wave replacement and explicit
rework retain invalidation semantics; a semantically unchanged replacement is
recorded as `unchanged` instead of failing after gate writes, and relative
future steps remain monotonic. Human-readable language aliases such as
`English` normalize before ledger creation. `manage_orchestration` is reserved for
inspect/resume/deactivate, lanes, resources, durable questions, and confirmed
project-scoped prune, active-task `steer`, and completed-task `follow_up`; it is not
part of normal wave progression. Host `spawn_agent` and user-authorized
`create_thread` are still performed by Codex, never by public MCP lifecycle
calls.

Every task-scoped `continue_orchestration`, `manage_orchestration`, recovery,
and report-read call must carry that exact opaque `task_ref`. Unscoped
management never inspects, lists, infers, or selects a task from the project
root and fails closed with `task_ref_required`. A start result with `ok=false`,
`task_created=false`, or no `task_ref` created no recoverable task; the
coordinator stops and reports the blocker rather than recovering an older task.

Before the single start call, ordinary tasks must advertise non-empty
`task.acceptance_criteria` and `task.verification` lists grounded in the exact
request or verified authority. Harvest and exact knowledge-census routes are
the sole server-supplied exception: Cortex provides their exhaustive census
contract. If a caller cannot ground either list without inventing material
intent, it must ask the user before starting.

`manage_governance` owns initiatives, dependencies, immutable record history,
active snapshots, exceptions, and reviewed promotion. Project policy and
promotion records may use project scope; every other record requires an
initiative or task. A pending worker policy revision leaves the approved
predecessor active until coordinator approval, when the approved replacement
supersedes its revision chain. Sensitive records require an approved
exact-type policy with positive `retention_days` and allowed actor roles; the
server derives or bounds expiry, enforces optional field/redaction lists, and
keeps expired rows only in audit history. Full-governance close review must
resolve from its immutable artifact to a linked task, passed `code_reviewer`
`governance_close` attempt, matching report reference, and completed native
session. In automatic mode, C3 raises the effective governance mode to `full`;
the server inserts `governance_activation` first and `governance_close`
immediately before final close, then renumbers the complete resolved wave list
so every public lifecycle response retains an integer relative step.

The coordinator governance capability appears only in the original successful
start response. Its SHA-256 digest and a separate recovery-proof digest are the
only durable verifiers; idempotent replay cannot recover or reissue either
value, and legacy plaintext values are scrubbed and invalidated. A lost initial
start response remains fail-closed until a host-attested delivery identity is
available—public task/principal/thread identifiers never substitute for a
proof. Recovery is accepted on the normal compatibility or explicit coordinator
audience with the exact active principal, thread, task, and original
non-durable recovery proof. It stages a single HMAC-derived replacement pair;
the same proof safely redelivers that pair after a lost response, while the old
generation remains active. `acknowledge_coordinator_recovery` must return the
prior proof and both replacements before it atomically commits the new
generation. An explicit worker audience cannot invoke either phase.
`governance_mode=off` is accepted only for C1 with a complete boolean
assessment of every documented hard and topology trigger, which is included in
the policy snapshot. Text classification can only raise the governance floor.

Governance v11 reads each record body from its immutable content artifact and
checks the digest and cached JSON before policy evaluation. Normalized scope
keys, exact task/initiative links, one-successor revision chains, strict JSON,
and immutable-field triggers reject cross-scope, sibling-revision, mutation,
and non-finite-number corruption. An append-only cryptographic lifecycle chain
is authoritative for status and approval basis. Pre-v10 migration reconciles
only deterministic v9 duplicate revisions/sibling successors and fails closed
for ambiguous scope or predecessor graphs. `submission_id` command digests
make record and revision retries conflict-safe. Linked milestone/deliverable
tasks must be `completed` before initiative completion/closure, and governed
initiative-task links cannot be deleted.

Cortex keeps each new v5 task on a generated task-local authorization identity.
The synchronous `PostToolUse` hook separately binds its returned `task_ref` to
the documented hook `session_id`; environment identity is only a compatibility
hint. `SessionStart` handles `resume`, `clear`, and `compact` and exposes model
context through `hookSpecificOutput.additionalContext`. If several active
tasks share one host session, Cortex removes the session lookup until one task
remains, so recovery context cannot be injected for the wrong task.

Cortex returns `task_ref` on every task-bound lifecycle response. The
coordinator preserves it on every later lifecycle and report-read call.
Different task contracts can run concurrently below one project root. The same exact
`task.user_request` cannot create another active task when only coordinator
metadata or proposed waves differ: it replays the existing task with
`replayed: true` and no dispatches. A different user request creates a distinct
task. Replayed continue calls also return no dispatches and cannot authorize a
duplicate wave. Omitting the ref always returns `task_ref_required`; task-
scoped calls never infer, inspect, list, or select an active project task. The
project registry is lock-serialized so concurrent process starts do not
overwrite one another.

An active or blocked task accepts a user correction through
`manage_orchestration(intent="steer")`. Cortex increments `task_revision`,
stores original and canonical-English messages, computes a bounded impact
summary, and returns `followup_task` calls only for active attempts with an
addressable native `host_agent_id`; the same attempt/session is resumed rather
than replaced. If the native session is unavailable, the steer remains durable
and the coordinator must inspect/continue the revised pipeline. A completed
source is immutable and uses the separate linked `follow_up` task route.

Revision impact is classified from the canonical English steer message. The
earliest affected gate and all downstream evidence are invalidated, while
documentation-only changes remain scoped to documentation. Worker questions
carry task/plan revision and strategy-generation identity; unresolved old
questions are superseded and cannot be answered after a material steer.

A user request to correct a task that is already `completed` uses
`manage_orchestration(intent="follow_up")` with the exact completed source
`task_ref` and `payload.user_request` containing the user's exact corrective
request. Cortex never reopens or mutates the source task. It creates a new
corrective task with its own `task_ref`, waves, approval policy, verification,
and close. The new task stores a `cortex/follow-up/v1` relation to the source,
including source-derived handoff and selected report Markdown paths; its
workers receive those paths as historical evidence and must revalidate current
source rather than treating old reports as authority. `payload.report_refs` is
an optional bounded list of source reports; omitted selects the latest bounded
set. A follow-up against an active source fails closed so the coordinator uses
normal active-task rework instead. Repeating the exact follow-up request is an
idempotent replay of the existing corrective task. If the coordinator had
deactivated between attempts, Cortex restores the server-owned activation for
that linked replay, returns the same opaque `task_ref`, and emits no duplicate
dispatch. The coordinator must not reopen the completed source or expose an
internal `/cortex` activation diagnostic to the user.

Language is split between the user-facing coordinator and internal workers.
The original user language is retained by the main coordinator only; every
worker message, tool argument, report, question, handoff, and native final
response is English. Hidden `spawn_agent` dispatches use `fork_turns: "none"`
so a localized coordinator transcript cannot override that boundary. Durable
worker questions remain English in the ledger.
The main coordinator may provide `localized_question`, `localized_header`,
`localized_options`, and `localized_custom_label` as transient chat projections
in the user's language without changing the durable question. A `follow_up`
task inherits the completed source task's `user_language`, while its workers
still follow the same English-only internal boundary.

Post-plan user review is controlled by `task.plan_approval`, which accepts
`auto` or `required`. The default is `required` for C2/C3 user tasks and `auto`
for C1; knowledge routes (`harvest` and `harvest-refresh`) force `auto`. Those
routes still run their internal `plan` phase, but never pause for post-plan user
approval. For ordinary tasks, a required plan must be the only phase in its
wave. After that plan succeeds, Cortex returns
`outcome: awaiting_plan_approval`, dispatches no successor, and includes a
bounded `plan_review` containing objective, work packages and microtasks,
paths, dependencies, verification, material risks, `report_ref`,
`remaining_phases`, and the derived absolute
`report_markdown_path`. The coordinator reads
the referenced planner report, gives the user a concise main-chat summary,
and waits for an explicit decision. It resumes with
`manage_orchestration(intent="plan_approval", payload={"decision":"prompt"})`,
which returns a `cortex/chat-interaction/v1` ordinary-chat hold with an opaque
request ID, approve/revise/cancel meanings, and the LLM-recommended response
with its rationale. The coordinator renders the complete summary as one final
assistant message, calls no UI/input/approval/elicitation tool, and ends the
turn. The next user message is recorded with the exact request ID. Revision
text is preserved verbatim and reruns Planner; approve authorizes the next
wave. Mismatched, stale, or replayed responses are rejected without dispatch.
Cancel leaves the plan in `awaiting_user`; silence never
infers approval. A material future-wave replacement or any pipeline operation
that reopens `plan` preserves the previous plan and approval in history, resets the
status to `pending_plan`, and requires a singleton replacement Planner followed
by another approval. No-op and transport-only replacements keep approval valid.
A revision uses
`payload={"decision":"revise", "feedback":"..."}` with non-empty feedback
and reruns the Planner before another approval hold. This is distinct from
the worker-question lifecycle: material questions are still resolved through
`worker_question` while planning, and do not become a second approval flow.

The Planner may also include a separate public `planning` object in its
`record_report` call. The strict `cortex/report/v1` report remains unchanged
at seven fields; `planning` must contain exactly `overview` and
`work_packages`. Each package has `id`, `title`, `objective`, optional
`allowed_paths` and `depends_on`, and at least one `microtasks` entry. Each
microtask requires `id`, `title`, `objective`, non-empty
`acceptance_criteria`, and non-empty `verification`, with optional `profile`,
`allowed_paths`, and `depends_on`; package-level `profile` is forbidden.
Microtask IDs are globally unique across the plan. `depends_on` may reference
microtasks in another work package; the combined microtask dependency graph is
validated as an acyclic DAG, and unknown references are rejected. Bounds are
32 packages, 32 microtasks per package, and 128 total microtasks.

Only Planner Scope may publish the additive top-level `scoping` sibling. It
contains exactly `overview`, `context_files`, and `discovery_domains`. Each
domain has a unique id and title, objective, project-relative paths, context,
dependencies, non-empty acceptance criteria, and non-empty verification. The
server rejects duplicate domain ids, dependency cycles, unsafe paths, more than
eight domains, or incomplete criteria. Scope is read-only and evidence-
gathering; it does not close intent questions.

The read-only Planner only proposes this durable planning catalog. Cortex
authorizes and queues immutable, revision-scoped projections under the
host-private state root's `tasks/<task>/planning/revisions/plan-<report-ref>/`, including
`overview.md` and `packages/<id>.json`. The SQLite task document
`planning_current` is the sole current-plan pointer; there are no
`planning/manifest.json` or `planning/overview.md` latest aliases. When a
required approval hold is reached,
`plan_review.planning_artifacts` exposes compact manifest and package metadata
for user review. The catalog supports ownership- and dependency-aware
scheduling; it does not create an unconstrained auto-executor outside the
canonical phase/wave safety model.

The coordinator builds or consciously accepts the initial pipeline and follows
the returned snapshot by default. Planner and explorer findings are advisory;
only the coordinator may replace not-yet-started `future_waves`, and only when
verified evidence materially changes ownership, dependencies, risk,
sequencing, or validation. Every replacement includes a concise reason.
The server always appends every verified scope, discovery, architecture,
database-architecture, and UX report required by the final Planner, regardless
of a compact future-wave context filter. If downstream future-wave validation
fails after the completed gate was durably recorded, the coordinator may retry
the same step and results with only `future_waves`, `reason`, or `rework`
corrected; it is not trapped behind the rejected payload's receipt.
Bounded phase aliases normalize `implement` to `implementation` and
`build_verification` to final `close`; the server also rejects a canonical
phase repeated across later waves, preventing correction/retry loops caused by
relabeling the same work.

The v5 adapter derives `allow_rework` whenever supplied `future_waves`
reintroduce a current or completed phase. This keeps the audited internal
transition explicit without requiring the coordinator model to remember a
redundant boolean. It also rejects a replacement that drops a still-pending
implementation phase. The task retains its initial `pipeline_obligations` so a
closure finding can restore phases no longer present in `current_pipeline`.
Corrective findings never exhaust a pipeline retry budget: repeated rework
continues with higher effort until the finding closes or an explicit
non-retryable blocker is recorded. For compatibility only, a task persisted by
an older runtime with `automatic close rework budget exhausted` is atomically
recovered through plan, implementation, applicable QA/security/performance/review,
documentation, and close, invalidates stale downstream evidence, and reopens
plan approval. The same completeness check runs before any
documentation or close dispatch when the accepted planning catalog requires
implementation but verified implementation and successor attempts are absent.
Request-shape validation failures state that no attempt budget was consumed.
Material public replans are evidence-backed transitions and therefore have no
task-lifetime quota. `replan_count` remains an audit counter, while the old
`replan_limit` value is accepted only so persisted tasks remain readable.
Per-gate counts and strategy names remain audit and escalation evidence; they
never cap repeated corrective work.
The liveness boundary is instead a no-progress circuit breaker: when the
finding fingerprint, affected paths, manifest/dispatch digest, verification
result, and failure class remain materially identical for the configured
repeat window, autonomous work pauses for an explicit user strategy. The
failed gate and evidence remain durable; the pause never becomes a false pass.
Recovery must begin with one singleton Planner wave and materially change the
failed pipeline, strategy, or verification contract. An infrastructure or
environment pause may instead name a class-matched remediation in that Planner
wave. Free-text `reason` prose is retained only as an audit digest and cannot
release the pause.
Before recording the current attempt or gate, the engine preflights pending
implementation retention, completed-gate rework, and singleton Planner
reapproval. If an older runtime already left an active current gate with no
live or pending dispatch, one Planner-first resume replacement repairs it;
active recovery is rejected whenever a worker is still addressable.

The control plane owns dynamic sequencing. It selects corrective waves, blocks
environment or policy conditions, carries the originating report into
corrective and originating-gate verification context, and requires that gate's
finding to be resolved before downstream waves can start. Workers report
observed facts and evidence; they do not choose remediation, the next action,
or a target gate.

While a Cortex task is active, the main/root agent is coordination-only. It
may use Cortex lifecycle calls, launch only the exact returned worker
dispatches, wait, evaluate reports, route questions, and communicate with the
user. It must never inspect, search, read, edit, patch, build, test, or run the
target project, Cortex plugin source/cache, `.codex` state, or runtime
internals. For every public tool, bundled instructions, public schemas, and
exact returned responses are authoritative; code/cache inspection is forbidden.
It must remain idle while workers run. Worker delay, failure, or unavailability
is handled through recovery, rework, or a blocker; it never authorizes direct
root project work or source inspection. `SessionStart` and every public v5
`next_action`, including caller-correctable failures, reassert this lock.

Host spawn prompts use one JSON-serialized, explicitly untrusted assignment
envelope; task-controlled text cannot become headings, fences, or protocol
instructions. `start_orchestration.next_action` is serialized before dispatch
payloads. Representative budgets are 1,500 bytes for the compact native
bootstrap, 16/24 KiB for ordinary briefings, and 18/28 KiB for harvest
briefings (soft/hard). Ordinary uses the top of the recommended 14–16 KiB soft
and 20–24 KiB hard ranges; the harvest overlay uses the expanded 16–18 KiB
soft and 24–28 KiB hard ranges. Validator and regression lints continue to
cover duplicate prompt paragraphs and adversarial assignment data. A worker is not considered sent
until native `spawn_agent` returns a child id; the coordinator must not announce
a dispatch or call wait with an empty target list, and native dispatch failure
is a blocker. Worker prompts have
three deliberate layers: the role-specific professional
playbook from the selected profile, the overall task assignment and context,
and the current gate mission with its ownership, acceptance, and verification
defaults. Task-level requirements and validation stay distinct from gate-level
criteria. Explicit coordinator-supplied objective, ownership, acceptance, or
verification values override the corresponding gate defaults; omitted values
are filled from the validated briefing registry. Context files and explicitly
granted predecessor reports are included in the assignment so workers can
ground their work without inventing missing context. A gate-level mission or
proposed criterion never expands the preserved user-authored intent boundary.

Every coordinator and worker maintains a turn-local evidence index and reads
each exact skill or file path at most once per turn. A second read is allowed
only after explicit truncation/pagination, a post-read edit, or a distinct
unread range; unchanged content is reused across later steps and tool calls.

The `planner` profile is read-only and follows a repository-grounded,
decision-complete workflow: it resolves discoverable facts, separates
low-impact reversible choices from material product decisions, closes
interfaces/data flow/failure, compatibility, validation, rollout, and
ownership concerns, and asks whenever a missing answer materially changes
scope or behavior. It may not turn recognized intent ambiguity into an
assumption. Its plan must leave the implementer no unmade design decisions
after those questions are answered and must cite evidence for consequential
choices.

A deterministic preflight recognizes short underspecified product-surface
creation requests. It marks the task as requiring intent clarification and
places the exact user request and blocking reason in the JSON-serialized,
untrusted assignment data of every worker briefing.
Discovery may gather bounded evidence needed to ask well, but plan and all
other decision-bearing phases cannot report completion until a blocking
`worker_question` has been answered and the same attempt resumes. A detailed
request bypasses this automatic hold; material ambiguity discovered during
work still uses the same question lifecycle. Existing project artifacts prove
current state, not the user's desired outcome.

Automatic implementation routing examines only bounded explicit signals in
the task objective, requirements, acceptance criteria, scope, allowed paths,
and verification. It recognizes relevant English and Russian signals and
selects conservatively in this order: `fullstack_dev`, `mobile_dev`,
`devops_engineer`, `data_engineer`, `debugger`, `refactorer`, `frontend_dev`,
then `backend_dev`, with `general` only when no specialist signal is strong
enough. The initial selection is provisional rather than a substitute for
repository evidence: `planner` or `explorer` may recommend a narrower owner and
the coordinator alone decides whether to replace not-yet-started
`future_waves`, with a concise evidence-based reason. The selected worker
receives only its role playbook and task-relevant routing context; the root
orchestrator remains coordination-only. Harvest-only specialization is supplied
by conditional `mode_overlays.harvest` entries in `profiles.json`, keeping
ordinary profile prompts focused.

The compact public worker schema exposes the exact enum of all 21 canonical
profile names. Legacy aliases remain compatibility input only. Cortex rejects
a profile that cannot own the requested phase before creating ledger state.
Every dispatch reports `phase`, `profile`, `capability`, `sandbox`, and
`selection_reason` separately from the unchanged native `call` arguments, so
the coordinator can audit routing without rewriting the host request.

Compact native dispatch responses carry `dispatch_ref`, one immutable scoped
`briefing_path`, and its SHA-256 digest. The worker reads exactly that path,
adds `Dispatch briefing reviewed: <sha256>` as a report evidence marker, and
must not directly read any other Cortex ledger path. The complete prompt is
never persisted in mutable coordination state; predecessor
reports remain available only through scoped `read_worker_report` reads.

The machine-validated profile contract also records required inputs, the
project artifact each profile owns, and its completion deliverable. The attempt
baseline is enforced by the ledger and report validation: implementation phases
reconcile real writes, while read-only profiles and phases must leave the
project unchanged. It is not repeated as prompt-baseline metadata. This
includes `build_verification` when assigned to the read-only `qa` profile;
sandbox enforcement rejects writes that violate the contract.

Every worker calls `worker_question` and `record_report`; a successor may also
call `read_worker_report` only for predecessor refs explicitly supplied in its
dispatch, with exact `project_root`, `task_ref`, `attempt_id`, and `profile`.
Cortex rejects ungranted refs and emits no coordinator Markdown-link
instruction in worker context. A material
question is persisted with action `ask`; the worker returns
`QUESTION_RECORDED question_ref=<value>` plus a complete decision handoff that
states why input is needed, every full self-contained question, every concrete
outcome-based option with descriptions and trade-offs, and the recommendation.
Generic numbered, A/B, or recommended/alternative placeholders are rejected.
Every question carries a required rationale and exact recommended option IDs,
or a concrete recommended text answer. The coordinator passes only the
`question_ref` to `manage_orchestration(intent="question")`, renders the
returned interaction completely in the user's language as its final assistant
message, visibly labels the LLM recommendation, and ends the turn without a UI
tool. Cortex internally resolves task, attempt, profile, and native-thread
identity. After the next user message is durably recorded, the coordinator
resumes the exact worker through `followup_task`; the worker polls the same ref
and continues the same attempt.
Caller/input/schema validation from allowed worker tools is returned as a
structured correction and retried on that same attempt without consuming the
failed-worker budget. `get_report_template` and `worker_question` preserve this
contract instead of turning malformed requests into terminal errors; only
explicit non-retryable integrity, storage, permission, or unavailable-identity
failures are terminal.
Duplicate calls return the durable answer without reopening the chat hold. Open
blocking questions reject both report publication and wave
continuation. This applies to every profile, not only Planner. Repository facts
are investigated, low-impact reversible choices may be documented, and
material intent/product/security/irreversible decisions must not be guessed.
Localized labels are transient display projections; answers retain original
value/language and require canonical `answer_en` for localized free text.
Workers may submit 1–32 stable questions through `ask_batch` and poll the
same `batch_ref` with `poll_batch`; the coordinator retains one durable ref but
renders one detailed ordinary-chat question per task turn and then stops until
the user's next message. Each accepted answer is checkpointed before the next
question is rendered. Every question visibly labels the LLM-recommended answer
and its rationale. Batch projections use `localized_question`,
`localized_header`, `localized_options`, and optional
`localized_custom_label`; `question`, `header`, `options`, and `custom_label`
remain compatibility aliases. Every localized question and option must be
self-contained and outcome-specific; generic numbered, A/B, or
recommended/alternative placeholders are rejected, and option descriptions
may be rendered. Cancellation leaves the batch open and a later resume starts
at the next unanswered question. The localized chat message is a transient projection, and
localized free text requires canonical English translation before resumption.
A task revision supersedes an unresolved batch.

After questions are resolved, every worker calls `get_report_template`, replaces
its gate-specific placeholders in the exact private draft file, and calls
`record_report` with that file's ref. `get_report_template`
creates a fully structured JSON draft with mode `0600` and returns only
`draft_ref`, `draft_path`, and its expiry, never the report body. Writers edit
that exact file; a read-only worker may instead send a small RFC 7396 merge patch
or complete replacement through `record_report`. The same canonical content
checks run during recording; invalid records return field paths and fixes while
leaving the same file in place. Recording does not create a failed worker
attempt; a genuinely failed worker outcome contributes only to durable
effort/model escalation and never consumes a finite pipeline budget. A new template supersedes an old or expired
draft. The worker then sends its exact identity and `draft_ref` to one atomic
`record_report`, which rereads and revalidates current state,
and deletes the file and metadata only after commit,
persisting exactly seven fields:
`summary`, `findings`, `questions`, `changed_files`, `tests`, `evidence`,
and `uncertainty`. A worker report has no `next_action`; findings contain observed
facts and evidence, not remediation instructions or target-gate decisions. Final `questions` must be `[]`: material decisions complete
the durable question lifecycle first, while genuinely non-blocking evidence
limitations belong in `uncertainty`. Public report intake rejects a non-empty
questions list. Its successful native final is only
`REPORT_RECORDED report_ref=<value>` plus at most a two-sentence summary; a
tool failure returns only the exact error. Independent draft-shape mistakes are
returned together as `{path, message, fix}` diagnostics; later semantic
diagnostics use the same structure. The direct draft must remain a current-user
regular non-symlink file with exact `0600` mode; the runtime verifies the
opened descriptor and rejects, rather than path-repairs, an unsafe replacement.
Public worker `record_report` identity is exactly `project_root`, `task_id`,
`attempt_id`, and `profile`; `task_ref`, `dispatch_ref`, and `submission_id`
are coordinator transport fields and are rejected. Required result evidence
markers retain their exact generated prefixes; a missing-marker diagnostic
supplies the exact marker and criterion to add with concrete observed proof.
A changed draft file requires another
`record_report` call with the same ref. A non-retryable error or unavailable exact identity
remains a blocker. A legacy full-payload `record_report` remains
accepted for compatibility. Host-sandboxed read-only gates record ordinary
source deltas observed in the shared checkout as concurrency evidence rather
than attributing them to the worker; claimed `changed_files` must name only
paths changed relative to that exact attempt baseline, so pre-existing,
concurrent, or another-attempt paths fail
validation. Every ignored side effect is non-blocking at a read-only gate and
is stored as a digest-only audit receipt. Conventional cross-language
generated directories/roots/files, virtual environments, build output, and
bytecode additionally receive the `ephemeral` classification. Unknown ignored
paths receive an `unclassified` count and digest, so future frameworks do not
require an allowlist update merely to record a valid report. The coordinator
reads the full record through `read_worker_report` and advances with the ref,
never an inline report body. Reads are repeatable, but publication is an
at-most-once durable event: only the first complete coordinator read after the
matching native `SubagentStop` may return `publication_required: true`,
`report_markdown_path`, `report_markdown_link`, and `completion_update`.
The coordinator publishes that exact link once in the same user-language
message as a concise explanation of what completed and what happens next; a
bare link is forbidden. Early reads and rereads return
`publication_required: false` and no link. The path must never be guessed, substituted, or used to browse
unrelated files. If the worker is interrupted after persistence but before its
acknowledgement, `manage_orchestration` inspect returns the compact entry in
`available_reports`, including the same path, for recovery.

Review, governance activation, governance close, and final close reports
require a separate canonical top-level `gate_result` envelope with `decision`,
`failure_class`, `findings`, `verification`, and `workspace`. The older
top-level `closure` input is accepted only as a compatibility alias for those
gates; canonical artifacts retain `gate_result` only. Neither input is nested
in the strict seven-field report. Governance review evidence is server-owned,
bound to the consumed report receipt and independent reviewer identity, and
covers the gate's typed obligations in one immutable verified artifact.

A `pass` result contains neither an open finding nor a missing required
verification. The materialized `task_findings.source_evidence` records the
server-bound opening/resolution transition with report, receipt, immutable
artifact, attempt, gate, and **semantic task revision** references. The first
trusted open event is immutable authority: later repeated `open` observations
add evidence but cannot replace its verifier/gate. A resolved transition
additionally retains the exact origin report and the current server-bound,
finding-specific corrective-report refs it consumed. A corrective worker can
confirm its changed artifact but cannot resolve an inherited finding. Only a
fresh rerun of the gate that opened the exact fingerprint can report it as
`resolved`, and only when the immutable origin report and a separate current
corrective report are in that rerun's scoped predecessor handoff and the
matching server-recorded rework is still active for the current semantic task
revision. A different fingerprint, gate, stale attempt/revision, missing or
unbound correction, or unbound report fails at report intake without mutating
canonical finding state. An invalidated source receipt never serves as current
pass evidence; its immutable report artifact is retained only as bounded
historical provenance for the corrective handoff.

For C2/C3 close attempts, Cortex additionally requires at least one executed
test or verification result and a non-empty concrete summary of the observed
output or behavior. Completion markers such as
“not run” or “unverified” fail closed, and the report must map every task-level
acceptance and verification criterion to the observed evidence; a bare
completion assertion is insufficient.

Every non-empty `report.tests` item is an object with exactly `command`, `cwd`,
`exit_code`, and `evidence`; the command must be exact and reproducible, and
the required exit code is zero. The generated worker briefing and public tool
description mirror these report, planning, and start-contract constraints so
callers can correct one validation failure without guessing the schema.

### Context-compaction recovery

`manage_orchestration(intent="inspect")` returns a bounded
`cortex/context-handoff/v1` snapshot for a resumed or compacted coordinator.
It is rebuilt from the durable task definition, state, pipeline, evidence, and
report index and carries the goal, acceptance criteria, verified report refs
and exact links, decisions, changed files, decisive checks, blockers, and
next action. Preserve the opaque `task_ref`, inspect once after compaction,
and continue the existing relative step. Cortex explicitly forbids restarting
the task or replaying completed dispatches during this recovery.

The documented `SubagentStart` event binds the parent session, native
`agent_id`, observed `model`, and exact returned dispatch identity before a
worker is considered active; dynamic workers report generic
`agent_type=default`. The handoff separates `pending_dispatches` from `active_workers`:
only matching top-level `inspect` dispatches authorize new spawns, while
active workers expose exact persisted child wait IDs. Missing binding fails
closed; the coordinator never guesses an identity or waits on a replacement.

When inspect finds an active gate with no pending dispatches and a stopped
worker, the stop outcome is decisive. A stopped worker with
`lifecycle_status=report_recorded` and `host_report_refs` is completion-pending,
not active and not resumable. The coordinator must choose exactly one
`report_ref` and explicitly continue; the server verifies that ref against the
exact task, gate, stopped `attempt_id`, and current revision before
consumption. No report is auto-selected or implicitly approved, and the
stopped child is never respawned or resumed. Missing, stale, already-consumed,
or mismatched refs fail closed and require recovery; multiple valid candidates
remain audit-visible until the coordinator chooses one. A durable-question stop is
surfaced and resumes only through the exact persisted native worker. A
reportless stop is terminal failed as
`native_worker_stopped_without_report`: submit one failed continuation with
its exact `dispatch_ref`, `status="failed"`, and reason, then use only a fresh
top-level dispatch returned by Cortex. Never wait on, respawn, or send
`followup_task` to the dead child. Pipeline repair is unbounded while its
acceptance or findings remain unresolved. Each failure is retained as audit
and routing evidence; Cortex raises effort through `high`, `xhigh`, and `max`,
and after two failures routes eligible work to Terra. `next_strategy` remains
optional and evidence-driven rather than a retry permit.
The PostToolUse wait-recovery hook filters every matching reportless stop in
the current gate rather than assuming the newest attempt is relevant. An
earlier failed attempt therefore remains surfaced even when a later retry has
already completed; its exact failed receipt is still required before advance.

Native worker identity is separate from the canonical role label. Every
dispatch keeps `profile` canonical and sets a human-readable `display_name`
derived from the task domain in the user's request (for example,
`Planner Authentication`), without an ordinal or digest. Gate mission verbs
such as `plan`, `discover`, or `close` are not used as the display module.
`spawn_agent.task_name` is the lower-underscore equivalent with a uniqueness
ordinal and digest (for example, `explorer_auth_02_<digest>`), remains unique to the task
and attempt, and must satisfy the host's strict `[a-z0-9_]{1,80}` name contract.
The deterministic digest preserves uniqueness without copying durable IDs,
request text, or skill paths into the host-visible name. `followup_task` is
reserved for the exact confirmed native worker being resumed; a reused
`host_agent_id` is rejected for another attempt. Lifecycle hooks use the exact
returned dispatch identity to associate the opaque child ID with the issued
native task key and canonical profile before emitting worker context.

`manage_orchestration(intent="prune")` is project-scoped maintenance and must
omit `task_ref`. With exact `confirmation: "PRUNE"`, it creates a durable
tombstone only for a completed task whose last update is at least
`older_than_days` old (default 7). Active and blocked tasks survive regardless
of age. The task graph remains canonical until its safe artifact-tree removal
succeeds outside the state lock; only then does one final SQLite transaction
reconcile task, activation, operation, classification, resource-claim, and lane
records. A failed filesystem operation leaves a retryable tombstone and intact
canonical state. A classification receipt referenced by any retained task is
preserved. Recent completed tasks, lane objects, project source/docs, and plugin
content are preserved. Repeating prune is idempotent; Cortex has no implicit
clear-all route. With no retention period, the public route instead offers
`keep_1d`, `keep_7d`, `keep_30d`, or `full_reset`. The explicit `full_reset`
choice requires the exact second confirmation `RESET CORTEX`, is blocked by
active tasks, and removes only host-private Cortex state; project source and
documentation remain untouched. Legacy project-local state is not the current
ledger location.

Predecessor handoffs are an enforced worker contract. Omitted `depends_on`
selects all verified predecessors, an explicit phase list selects only those
completed or earlier-wave dependencies, and `[]` declares intentional
independence. Cortex dispatches the selected set's verified transitive DAG
frontier. A passed report covers only the exact refs that its attempt read and
acknowledged; covered reports remain immutable and continue to influence the
Planner evidence digest. Report bodies are not embedded in successor prompts:
the worker reads every frontier ref through `read_worker_report`, reconciles the
handoffs, and emits an exact generated `Predecessor review:` evidence marker
containing all refs. Public `record_report` rejects incomplete acknowledgement.
There is no separate predecessor-count limit. Compact inspect/recovery views
independently retain eight recent summaries.

Codebase Memory is conditional worker tooling rather than a ledger dependency.
Cortex precomputes the current upstream path-derived project key from canonical
task root and embeds it in every worker briefing. The worker uses that key
directly; only direct not-found, ambiguity, or apparent drift/collision permits
one `list_projects` fallback, and its entry must match the exact canonical root.
Workers prefer graph, architecture, and trace operations for discovery and
impact analysis and confirm consequential findings in source and tests.
`planner`, `explorer`, `architect`, and `database_architect`
may refresh one missing or stale index; other profiles fall back to ordinary
repository tools after one failed attempt. No profile loops on setup, and the
main/root coordinator must not use Codebase Memory to inspect the project.

Repository knowledge is an enforced worker context layer. Cortex adds
`docs/project/index.md` and `docs/features/index.md` to every briefing when
they exist. The planner selects task-relevant linked pages and recommends
their exact paths; the coordinator attaches them to future compact workers
through `context_files`. All workers re-check the indexes, treat documentation
as navigation and prior knowledge rather than authority, verify consequential
claims in source, tests, schemas, migrations, or executable configuration,
and persist `Knowledge reviewed:` evidence. Public report intake rejects a
missing index acknowledgement. Explicit context paths must be existing
project-relative regular files; absolute, traversing, missing, and symlink
paths are rejected.

Knowledge-harvest objectives force the canonical `scope`, `discover`,
`architecture`, `plan`, `documentation`, `review`, and `close` pipeline. The
Planner Scope report publishes a discovery brief, context files, and up to
eight non-overlapping domains; the final Planner consumes all predecessor
reports. Incremental
harvest is valid only after a current source-backed coverage manifest proves a
zero-gap baseline. Otherwise the coordinator runs a full feature census, uses
2–8 non-overlapping domain explorers for a large repository, synthesizes stable
feature boundaries, writes behavior-complete pages, and requires an independent
review with zero unexplained unmapped surfaces. Refresh rebuilds the inventory
and also requires a no-change second documentation plan. Documentation,
review, and close reject a shallow feature index without Coverage matrix
columns, Inventory totals, Unmapped surfaces, Exclusions, or Known unknowns.

Baseline manifests honor project `.gitignore` files, applying rules in order
and including negations, and store the discovered rules in the baseline policy.
Reconciliation reuses that frozen policy for task stability. In addition,
language-agnostic high-confidence dependency, cache, test-output, and runtime
directories are excluded automatically. Names that can be either source or
generated output (`build`, `dist`, `out`, `target`, `bin`, and `obj`) are excluded
only with an applicable ignore rule or recognizable build marker; conventional
generated roots, virtual environments, and `.pyc`/`.pyo` bytecode are likewise
recognized by the manifest. Arbitrary ignored paths remain visible to the
read-only result validator. Symlinks are recorded but never followed. Thus the
receipt remains an independent local changed-file check without treating
recognized ephemeral outputs as source changes.

Manifest capture is bounded by `max_entries`, `max_hashed_bytes`, and
`max_seconds`; a limit produces a partial result with a reason. If the baseline
or current capture is partial, comparison/reconciliation is incomplete and
cannot authorize read-only mutation auditing, a complete handoff, or terminal
close. A bounded digest cache reuses unchanged hashes
only when the full stat identity matches. CI runs
`scripts/cortex-manifest-benchmark.py --files 50000 --max-seconds 30` and
requires its JSON `target_met` field to be true.

Every task-start and per-attempt manifest capture is normalized into an
immutable content-addressed `cortex.db` record. The snapshot address
includes the root, frozen policy, entries, entry count, and manifest digest;
compact `manifest-<sha256>` refs in task/attempt state are validated against
that content. Repeated equal captures deduplicate, but delegation still captures
current project state before every dispatch. On completed close, Cortex first
persists terminal state and then removes these database records. Final receipts
retain baseline/current digests and
the changed-path proof. Reopening with `allow_rework` creates a fresh active
baseline rather than depending on cleaned terminal artifacts.

Reports are sanitized, task- and attempt-bound, and use one-use receipts.
Consuming a receipt writes an irreversible `reports/consumptions/` tombstone,
so reconciliation can repair derived receipts, indexes, and Markdown but
cannot replay consumed evidence. A report is capped at 64 KiB and 100 list
items per field, and an attempt at 32 reports. Tasks have no report-count or
aggregate-report-byte quota; immutable history grows in SQLite until storage
is unavailable or project-scoped state is explicitly pruned. Task and operation ledger files
have an 8 MiB upper bound. Ordinary JSON writes use `MAX_JSON_BYTES=8 MiB` and
fail before replacement with actionable diagnostics. Manifest snapshot reads
use `MAX_MANIFEST_BYTES=64 MiB`; initial capture preflight runs before task-directory creation,
and handoff/reconciliation snapshot serialization remains bounded, so oversized
artifacts fail closed rather than surfacing at close. Every call includes an absolute
`project_root`; the same server process may serve multiple roots. Mutating v5
operations use server-owned request-digest receipts tied to the internal
active wave, so identical retries replay and changed or stale payloads
conflict before partial writes. Expected public v5 validation and recovery
outcomes return structured `ok: false` responses with bounded diagnostics and
a corrective `next_action`; because these are caller-correctable protocol
results, they do not enter the exception log. Exceptions raised at the MCP
boundary remain redacted and logged. Host model/tool/effort values
are selected routing metadata; v5 does not claim actual host attestation
unless the host supplies observable evidence.
Profiles and all scope/plan-aware gate briefings are preloaded and validated at MCP startup;
invariant coverage checks that all 21 playbooks contain the required
professional sections and that every gate briefing has non-generic acceptance
and verification lists. Runtime validation also checks complete routing
metadata, TOML identity/description/sandbox parity, and unique implementation
specialist rules. Recovery and nested
operations are `inspect`, `resume`, `deactivate`, `lane`, `resource`,
`question`, `steer`, and `follow_up`.

Ledger, report-bus, and journal paths reject symlink ancestry and require
regular-file targets, so journal or report-bus links cannot redirect state
writes. Metrics reject negative token/elapsed values and non-finite or negative
costs; telemetry retains a bounded tail of 1,000 events or 512 KiB and records
evictions in `telemetry_dropped`.

Multi-agent v2 is required for explicit per-worker model selection. `explorer`
always selects Luna with coordinator-selected effort or a risk-based default;
Terra is only its host-unavailable fallback. The accepted effort vocabulary
ends at `max`. The model contract classifies ordinary profiles as efficient,
adaptive, or deep: efficient work uses Luna, deep profiles and
`terra_task_kinds` entries use Terra. C2/C3 planning and those entries
(including uncertain
diagnosis, long-context, and integration-conflict work), plus high/critical
failure cost, also use Terra; other low/moderate-risk adaptive work stays on
Luna. Efficient Luna uses
C1/C2/C3 `high`/`high`/`xhigh`, bounded adaptive Luna uses
`high`/`xhigh`/`max`, and Terra uses `high`/`high`/`xhigh`, subject to the risk
floor. Automatic `max` covers C3 adaptive Luna work and repeated unresolved
corrective failures. Security
context, the security gate, and `security_auditor` always select Sol with the
complexity floors above. Non-security Sol requires matching
`user_requested_model` and `requested_model` from an explicit user choice; old
`sol_escalation`, auditable-extreme, failed-Terra, and model/effort-remap
authorization is rejected. Configured-default Luna omits native `model`, while
a hidden host-unavailable Terra fallback preserves selected effort.

Classification receipts are authoritative at initialization, so duplicate
complexity and requirements inputs are ignored. Host completion and gate proof
are separate: a passed attempt may be finalized before evidence linkage, while
the gate remains blocked until required evidence is recorded. A unique
context-grant id supplied where a report receipt is expected is corrected to
that report's one-use receipt. Other `commit_gate` validation failures are
recorded as bounded recovery events; after three failures for the same
gate/mode the task becomes `blocked` with an explicit handoff/resume action
instead of remaining active forever. This is a private atomic `commit_gate`
adapter safety cap only; it does not limit pipeline rework, QA findings, review
corrections, worker retries, or closure cycles. Lanes support creation, leases, task
binding, resource claims, optional declared-worktree materialization,
reconciliation, and clean retirement; managed dirty worktrees are refused
during retirement.

## Verification

The focused plan-approval, replan, recovery, and read-only artifact regressions
described here are historical 9.2.4 source evidence; they do not certify the
9.2.11 hardening release candidate. The cold-boot smoke uses the public JSON-RPC server to reject
implementation loss, apply three dynamic pipeline changes beyond the persisted
legacy replan limit, and verify every resulting gate through close. Complete
discovery validation remains a separate governance-v10 workstream and is not
claimed here. These checks exercise the source
MCP server and mocked/native JSON-RPC exchanges; this checkout does not include
a live Codex Desktop renderer, so installed-plugin and live-host button
rendering remain separate release/integration checks. Related commands and
boundaries are in [verification.md](../../project/verification.md).
<!-- GENERATED:END -->
