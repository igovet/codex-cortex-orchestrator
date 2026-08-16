# Orchestration ledger, report bus, and lane lifecycle

<!-- GENERATED:START -->
## Purpose

The local MCP server implements the Cortex 6.1.2 `cortex/v8` task ledger and
public `cortex/orchestration/v4` lifecycle, staged waves,
worker questions/reports, maintenance, and optional execution lanes through exactly seven public
tools: coordinator lifecycle operations `start_orchestration`,
`continue_orchestration`, and `manage_orchestration`, worker
`worker_question`, `record_report`, exact identity/digest-scoped immutable
briefing fallback `read_dispatch_briefing`, and scoped predecessor
`read_worker_report`.
Legacy v7/v3 ledgers and facades are unsupported: they are not migrated or
resumed and must be recreated.

## Key files and dependencies

- [cortex.py](../../../plugins/cortex/scripts/cortex.py) implements task, report, and lane tools.
- [profiles.json](../../../plugins/cortex/profiles.json) is the canonical machine-validated source for all 21 profiles, their descriptions, sandboxes, route categories, gates, selection/avoidance guidance, adaptive model/effort routing, ordered implementation routing, 13 gate briefings, and the `cortex/report/v1` field contract.
- [test_cortex_control.py](../../../tests/test_cortex_control.py) covers report-bus scoping/reconciliation and lane lifecycle behavior.

Repository-root `AGENTS.md` is development-only and is not installed. Runtime
guarantees come exclusively from the installable `plugins/cortex/` tree.

## Canonical artifact layout

The root ledger owns `.state.lock`, `activations.json`, `host-sessions.json`
when bound, `task-index.json`, `orchestration-operations.json`, transaction
files under `operations/`, retained classification receipts, and optional
resource claims and lanes. A task owns `task.json`, `current.json`,
`orchestration.json`, `baseline-manifest.json`, per-attempt delegation and
baseline files, report records/Markdown/receipts/consumptions and indexes,
evidence records, optional questions, planning pointers and immutable
revisions, handoff JSON, and append-only journal/audit files. These separate
runtime state, recovery checkpoints, immutable evidence, and human audit.

The runtime deliberately does not create `v3-operations.json`, active-task or
status-receipt files, `reports/grants`, `metrics.json`, task lock files,
handoff-manifest snapshots, or evidence-snapshot files. `baseline-manifest.json`
is intentional: the task baseline proves whole-task handoff stability and each
attempt baseline reconciles actual changed files, including in non-Git or
pre-existing-dirty repositories.

## Behavior and status

`start_orchestration` accepts an absolute `project_root` and requires the
user's exact, unexpanded text in `task.user_request`. Desktop's sole
host-metadata exception is its injected
`[$cortex:orchestrator](absolute-local-plugin-path/skills/orchestrator/SKILL.md)`
wrapper, which Cortex canonicalizes to `$cortex:orchestrator` before task
identity, labels, persistence, and worker prompts. The route and every
following user-authored word are preserved; arbitrary Markdown links and user
paths remain unchanged. This prevents local plugin-cache paths and
cache-version changes from entering durable task state. This is the breaking
4.0.0 intent boundary. Deprecated `task.objective` is optional compatibility
input and, when present, must match the trimmed `user_request` exactly;
paraphrase or coordinator expansion is rejected before ledger writes. Cortex
defaults complexity to safe C2, builds the standard pipeline when waves are
omitted, and prepares the first wave. Each
`continue_orchestration` call supplies the relative active-wave `step`, the
opaque `task_ref`, and persisted worker `report_ref` values. A single-worker
wave needs no slot; a parallel wave
uses short relative `worker: 1..N` slots. The server validates completeness,
uniqueness, and ownership atomically before state writes, then returns the
next step and native dispatch arguments. Future-wave replacement and explicit
rework retain invalidation semantics; a semantically unchanged replacement is
recorded as `unchanged` instead of failing after gate writes, and relative
future steps remain monotonic. Human-readable language aliases such as
`English` normalize before ledger creation. `manage_orchestration` is reserved for
inspect/resume/deactivate, lanes, resources, durable questions, and confirmed
project-scoped prune, and completed-task `follow_up`; it is not
part of normal wave progression. Host `spawn_agent` and user-authorized
`create_thread` are still performed by Codex, never by public MCP lifecycle
calls.

Cortex keeps each new v4 task on a generated task-local authorization identity.
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
duplicate wave. Omitting the ref when several tasks are selectable returns
`needs_selection` with bounded objective/ref candidates. The project registry
is lock-serialized so concurrent process starts do not overwrite one another.

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
`localized_options`, and `localized_custom_label` as transient UI projections
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
bounded `plan_review` containing `report_ref`, `summary`, `findings`,
`uncertainty`, `next_action`, `remaining_phases`, and the derived absolute
`report_markdown_path`. The coordinator reads
the referenced planner report, gives the user a concise main-chat summary,
and waits for an explicit decision. It resumes with
`manage_orchestration(intent="plan_approval", payload={"decision":"approve"})`;
approval dispatches the next wave. A revision uses
`payload={"decision":"revise", "feedback":"..."}` with non-empty feedback
and reruns the Planner before another approval hold. This is distinct from
the worker-question lifecycle: material questions are still resolved through
`worker_question` while planning, and do not become a second approval flow.

The Planner may also include a separate public `planning` object in its
`record_report` call. The strict `cortex/report/v1` report remains unchanged
at eight fields; `planning` must contain exactly `overview` and
`work_packages`. Each package has `id`, `title`, `objective`, optional
`allowed_paths` and `depends_on`, and at least one `microtasks` entry. Each
microtask has `id`, `title`, and `objective`, with optional `profile`,
`allowed_paths`, `depends_on`, `acceptance_criteria`, and `verification`.
Package and per-package microtask dependencies are validated as acyclic DAGs,
with bounded limits of 32 packages, 32 microtasks per package, and 128 total
microtasks.

The read-only Planner only proposes this durable planning catalog. Cortex
materializes the validated artifacts under
`.codex/cortex/tasks/<task>/planning/`: `manifest.json`, `overview.md`, and
immutable revisions at `revisions/plan-<report-ref>/packages/<id>.json`.
`manifest.json` is the current pointer and source of truth; revisions preserve
prior approved or revised plans. When a required approval hold is reached,
`plan_review.planning_artifacts` exposes compact manifest and package metadata
for user review. The catalog supports ownership- and dependency-aware
scheduling; it does not create an unconstrained auto-executor outside the
canonical phase/wave safety model.

The coordinator builds or consciously accepts the initial pipeline and follows
the returned snapshot by default. Planner and explorer findings are advisory;
only the coordinator may replace not-yet-started `future_waves`, and only when
verified evidence materially changes ownership, dependencies, risk,
sequencing, or validation. Every replacement includes a concise reason.
Bounded phase aliases normalize `implement` to `implementation` and
`build_verification` to final `close`; the server also rejects a canonical
phase repeated across later waves, preventing correction/retry loops caused by
relabeling the same work.

While a Cortex task is active, the main/root agent is coordination-only. It
may use Cortex lifecycle calls, launch only the exact returned worker
dispatches, wait, evaluate reports, route questions, and communicate with the
user. It must never inspect, search, read, edit, patch, build, test, or run the
target project and must remain idle while workers run. Worker delay, failure,
or unavailability is handled through recovery, rework, or a blocker; it never
authorizes direct root project work. `SessionStart` and every public v4
`next_action`, including caller-correctable failures, reassert this lock.

Host spawn prompts first de-duplicate the exact user request, then add the
worker contract. `start_orchestration.next_action` is serialized before dispatch
payloads. The compact native bootstrap is below 1,500 bytes; the full immutable
briefing is regression-tested below 11,500 bytes and the complete public start response below 8,000 UTF-8
bytes to prevent Codex tool-output truncation. A worker is not considered sent
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
places the exact user request and blocking reason in every worker briefing.
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
`future_waves`, with a concise evidence-based reason. Both
profiles receive the complete generated team catalog; the root orchestrator
skill carries the same generated roster and routing rules while the root
remains coordination-only.

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
never persisted in mutable `current.json` or delegation JSON; predecessor
reports remain available only through scoped `read_worker_report` reads.

The machine-validated profile contract also records required inputs, the
project artifact each profile owns, and its completion deliverable. The attempt
baseline is included in the worker prompt: implementation phases must reconcile
real writes, while read-only profiles and phases must leave the project
unchanged. This includes `build_verification` when assigned to the read-only
`qa` profile; sandbox enforcement rejects writes that violate the contract.

Every worker calls `worker_question` and `record_report`; a successor may also
call `read_worker_report` only for predecessor refs explicitly supplied in its
dispatch, with exact `project_root`, `task_ref`, `attempt_id`, and `profile`.
Cortex rejects ungranted refs and emits no coordinator Markdown-link
instruction in worker context. A material
question is persisted with action `ask`; the worker returns only
`QUESTION_RECORDED question_ref=<value>` plus a concise summary, ends the
current native turn, and becomes idle/resumable. The coordinator passes only
that `question_ref` to `manage_orchestration(intent="question")`; Cortex
internally resolves task, attempt, profile, and native-thread identity and
opens native MCP elicitation. Guessed identity fields and prose fallback fail
closed. After the answer, the coordinator resumes the exact worker through
`followup_task`; the worker polls the same ref and continues the same attempt.
Duplicate calls return the durable answer without reopening the UI. Open
blocking questions reject both report publication and wave
continuation. This applies to every profile, not only Planner. Repository facts
are investigated, low-impact reversible choices may be documented, and
material intent/product/security/irreversible decisions must not be guessed.

After questions are resolved, every worker uses `record_report` to persist exactly `summary`,
`findings`, `questions`, `changed_files`, `tests`, `evidence`, `uncertainty`,
and `next_action`. Final `questions` must be `[]`: material decisions complete
the durable question lifecycle first, while genuinely non-blocking evidence
limitations belong in `uncertainty`. Public report intake rejects a non-empty
questions list. Its successful native final is only
`REPORT_RECORDED report_ref=<value>` plus at most a two-sentence summary; a
tool failure returns only the exact error. The coordinator
reads the full record through `read_worker_report` and advances with the ref,
never an inline report body. That read also returns Cortex's derived absolute
`report_markdown_path` and the exact `report_markdown_link` for
`reports/markdown/<report-ref>.md`; after reading each completed report, the
coordinator immediately publishes that link verbatim as a compact clickable
Markdown link before any other lifecycle call or additional report read. This
is mandatory coordinator output, in addition to the concise summary and report
review. The path must never be guessed, substituted, or used to browse
unrelated files. If the worker is interrupted after persistence but before its
acknowledgement, `manage_orchestration` inspect returns the compact entry in
`available_reports`, including the same path, for recovery.

For C2/C3 close attempts, Cortex additionally requires at least one executed
test or verification result and observed evidence. Completion markers such as
“not run” or “unverified” fail closed, and the report must map every task-level
acceptance and verification criterion to the observed evidence; a bare
completion assertion is insufficient.

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
`agent_id`, and observed `model` to the next sequentially issued dispatch
before a worker is considered active; dynamic workers report generic
`agent_type=default`. The handoff separates `pending_dispatches` from `active_workers`:
only matching top-level `inspect` dispatches authorize new spawns, while
active workers expose exact persisted child wait IDs. Missing binding fails
closed; the coordinator never guesses an identity or waits on a replacement.

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
`host_agent_id` is rejected for another attempt. Lifecycle hooks use required
sequential spawn order to associate the opaque child ID with the issued native
task key and canonical profile before emitting worker context.

`manage_orchestration(intent="prune")` is project-scoped maintenance and must
omit `task_ref`. With exact `confirmation: "PRUNE"`, it removes task-scoped
ledger state whose last update is at least `older_than_days` old (default 7),
including abandoned active tasks. Under the global state lock it reconciles
`task-index.json`, `activations.json`, `orchestration-operations.json`,
classification/transaction receipts, task resource
claims, and lane bindings before deleting the task directories. Recent tasks,
lane objects, project source/docs, and plugin content are preserved. Repeating
prune is idempotent; Cortex intentionally has no clear-all route.

Predecessor handoffs are an enforced worker contract. Omitted `depends_on`
supplies every verified predecessor report ref, an explicit phase list selects
only those completed or earlier-wave dependencies, and `[]` declares
intentional independence. Report bodies are not embedded in successor prompts:
the worker reads every granted ref through `read_worker_report`, reconciles the
handoffs, and emits an exact generated `Predecessor review:` evidence marker
containing all refs. Public `record_report` rejects incomplete acknowledgement.
Ref-based handoffs remain bounded and fail closed rather than silently dropping
older reports.

Codebase Memory is conditional worker tooling rather than a ledger dependency.
When the tools are available, the worker resolves the task project by exact
root through `list_projects`, prefers graph, architecture, and trace operations
for discovery and impact analysis, and confirms consequential findings in
source and tests. `planner`, `explorer`, `architect`, and `database_architect`
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

Knowledge-harvest objectives force the canonical `plan`, `discover`,
`architecture`, `documentation`, `review`, and `close` pipeline. Incremental
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
generated output (`build`, `dist`, `target`, `bin`, and `obj`) are excluded
only with an applicable ignore rule or recognizable build marker. Symlinks are
recorded but never followed. Thus the receipt remains an independent local
changed-file check without treating virtual environments or package stores as
source changes.

Reports are sanitized, task- and attempt-bound, and use one-use receipts.
Consuming a receipt writes an irreversible `reports/consumptions/` tombstone,
so reconciliation can repair derived receipts, indexes, and Markdown but
cannot replay consumed evidence. A report is capped at 64 KiB and 100 list
items per field; an attempt at 32 reports; a task at 256 reports and 1 MiB
total; and an attempt at 256 context grants. Task and operation ledger files
have an 8 MiB upper bound. Ordinary JSON writes use `MAX_JSON_BYTES=8 MiB` and
fail before replacement with actionable diagnostics. Baseline manifest reads
use `MAX_MANIFEST_BYTES=64 MiB`; preflight runs before task-directory creation,
and handoff/reconciliation snapshot serialization remains bounded, so oversized
artifacts fail closed rather than surfacing at close. Every call includes an absolute
`project_root`; the same server process may serve multiple roots. Mutating v4
operations use server-owned request-digest receipts tied to the internal
active wave, so identical retries replay and changed or stale payloads
conflict before partial writes. Expected public v4 validation and recovery
outcomes return structured `ok: false` responses with bounded diagnostics and
a corrective `next_action`; because these are caller-correctable protocol
results, they do not enter the exception log. Exceptions raised at the MCP
boundary remain redacted and logged. Host model/tool/effort values
are selected routing metadata; v4 does not claim actual host attestation
unless the host supplies observable evidence.
Profiles and all 13 gate briefings are preloaded and validated at MCP startup;
invariant coverage checks that all 21 playbooks contain the required
professional sections and that every gate briefing has non-generic acceptance
and verification lists. Runtime validation also checks complete routing
metadata, TOML identity/description/sandbox parity, and unique implementation
specialist rules. Recovery and nested
operations are `inspect`, `resume`, `deactivate`, `lane`, `resource`,
`question`, and `follow_up`.

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
floor. Automatic `max` is limited to bounded C3 Luna work. Security
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
instead of remaining active forever. Lanes support creation, leases, task
binding, resource claims, optional declared-worktree materialization,
reconciliation, and clean retirement; managed dirty worktrees are refused
during retirement.

## Verification

Run `python3 -m unittest discover -s tests -v`; the focused source-backed coverage is [test_cortex_control.py](../../../tests/test_cortex_control.py). Current 6.1.2 evidence is the 309-test suite, marketplace validation, Python compilation, shell syntax, cold boot, an isolated fresh-plugin probe, installed-content verification at `6.1.2+codex.<build>`, a completed native six-phase harvest, and a fresh final-build worker-identity dispatch; the installer preserves the user MCP approval override. Run tracked-release verification against the committed candidate before push. Related project commands are in [verification.md](../../project/verification.md).
<!-- GENERATED:END -->
