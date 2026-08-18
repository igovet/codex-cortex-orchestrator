---
name: orchestrator
description: Coordinate non-trivial coding work or source-backed repository knowledge harvesting with Codex custom agents. Use for C2/C3 features, debugging, reviews, migrations, Cortex help, incremental harvests, full harvest refreshes, or work that needs planning, delegated investigation, verification, and an evidence-based final integration.
---

# Cortex Orchestrator

## Invocation and routes

In Codex Desktop use the Skills picker to select `cortex:orchestrator` or
mention `$cortex:orchestrator`. In CLI use `$cortex:orchestrator` or `/skills`.
Bare `/cortex` and `/normal` are textual shorthand, not registered native slash
commands. Do not use the deprecated `/prompts` mechanism.

Never present a bare `/cortex` or `/normal` token as a required next step or
ask the user to send it as a recovery command. Those tokens are not native
host commands. If activation is needed, use the Skills picker to select
`cortex:orchestrator` or mention `$cortex:orchestrator`; to leave the route,
use `$cortex:orchestrator normal`. A Cortex lifecycle response that says a
task is complete is terminal: report the verified handoff and limitations
without asking for another activation.

| Exact argument | Route | Effect |
| --- | --- | --- |
| `empty` | `orchestrate` | Start normal relative orchestration. |
| `help` | `help` | Explain Cortex without writes. |
| `harvest` | `harvest` | Incrementally synchronize knowledge docs. |
| `harvest-refresh` | `harvest-refresh` | Fully re-audit knowledge docs. |
| `prune` | `prune` | Remove only completed project-local Cortex task state stale for at least seven days. |
| `normal` | `normal` | Exit the active Cortex session. |

Do not guess unknown arguments. Show help and ask the user to choose.

The help route explains invocation, opt-in behavior, the project-local
`.codex/cortex` ledger, the seven public v4 lifecycle/report tools, internal workers, and that
source/tests outrank generated docs. Help performs no activation, dispatch, or
write.

The empty, `harvest`, and `harvest-refresh` routes explicitly authorize durable
orchestration; `prune` authorizes only the bounded maintenance call below.
Ordinary work never activates Cortex. The normal route uses
`manage_orchestration` with intent `deactivate` only when a Cortex task is
active.

The `prune` route is maintenance, not a coding pipeline. After explicit user
selection, call `manage_orchestration` once with exact absolute `project_root`,
intent `prune`, no `task_ref`, and
`payload: {"confirmation":"PRUNE","older_than_days":7}`. It removes only
completed task-scoped `.codex/cortex` state last updated at least seven days
ago, and reconciles task indexes, public starts,
activations, operation receipts, classification receipts, task resource
claims, and lane bindings. It preserves every active or blocked task regardless
of age and never removes a classification receipt referenced by a retained
task. It also preserves recent completed tasks, lanes, source, documentation,
and plugin files. Never reinterpret `prune` as clear-all. When no retention
period is supplied, the route presents the stable choices `keep_1d`, `keep_7d`,
`keep_30d`, and `full_reset`. The first three map to bounded retention
windows. `full_reset` is separately destructive: it requires the exact second
confirmation `RESET CORTEX`, refuses to run while any task is active, and
removes only `.codex/cortex` state while preserving project source and docs.

## Harvest route contract

For exact `harvest` or `harvest-refresh`, read
`../knowledge-harvest/SKILL.md` and its linked
`references/feature-census.md` completely before calling Cortex. Those files
define the mandatory inventory, coverage matrix, feature-page depth, and
completeness gates. Do not substitute a generic documentation task.

Both routes start with the canonical phases `plan`, `discover`, `architecture`,
`documentation`, `review`, and `close`. After reading the planner report, the
coordinator must decide whether the repository is large enough to split the
single discovery placeholder into 2–8 parallel `explorer` workers with
non-overlapping domain ownership. A repository with several applications,
services, packages, runtime processes, or integration families is large for
this purpose. Each discovery worker declares `depends_on: ["plan"]`. After the
domain census, the architecture worker receives `plan` and `discover`; each
documentation worker receives the architecture synthesis plus any domain
handoffs it needs. Use non-overlapping documentation paths when parallelizing
writers and exactly one owner for `docs/features/index.md`.

`harvest` is incremental only after a source-backed coverage manifest proves a
complete baseline. If the manifest is absent, shallow, stale, contradicted, or
contains unexplained gaps, the coordinator must treat harvest as a full
baseline census. Recent commits may prioritize discovery but may never define
the entire scope of an incomplete baseline.

`harvest-refresh` always rebuilds the inventory independently of existing
feature docs. Its review worker performs a second source-to-doc coverage pass;
zero unexplained unmapped surfaces and a no-change second documentation plan
are required. Any gap triggers bounded documentation rework rather than a
successful close.

The coordinator must reject reports that lack inventory counts, domain/source
coverage, mapping/exclusion evidence, or a concrete next coverage action. A
handful of top-level service summaries is not complete documentation when
those services own distinct workflows, commands, state machines, integrations,
configuration, failure behavior, or operational contracts.

Both harvest routes always use `task.plan_approval: auto`. Planning remains a
worker phase with a durable planning artifact, but a command-style knowledge
harvest never pauses for separate user approval of that plan.

## Coordinator isolation invariant

While Cortex orchestration is active, the main/root agent is a coordinator,
not a project worker. It must not inspect, search, read, edit, patch, generate,
format, build, test, or run project code itself. This prohibition includes
repository shell commands and direct filesystem or patch tools, even when the
next implementation step appears obvious from a worker report.

The coordinator may only clarify the user's goal, call the public Cortex
lifecycle and report-read tools, invoke the exact returned native dispatches,
wait for workers, relay questions, evaluate reports and gate evidence, adjust
future waves through Cortex, and communicate the integrated result to the user. All project
operations, including follow-up inspection and implementation after a planning
report, belong to a dispatched worker. The coordinator must remain idle while
a worker is active. Never work in parallel with an active
worker or substitute coordinator work for a missing, slow, failed, or blocked
worker. If dispatch is unavailable, stop and report the blocker; do not fall
back to direct project work.

## Team intelligence and routing

The main/root agent is the user-facing mission commander and integration
authority. It owns goal clarification, routing, wave decisions, evidence
gates, recovery, and the final answer. It is never a hidden implementer.
Workers are internal specialists with bounded ownership; they report to the
main coordinator and never become an alternate user-facing authority.

`profiles.json` is the canonical team source. Use only the exact profile names
below. `automatic` means the profile is the default owner of one or more
pipeline gates. `manual` means Cortex selects it for the implementation gate
only when task signals or worker evidence justify that specialist. Access is a
hard capability boundary, not a suggestion.

<!-- BEGIN GENERATED PROFILE CATALOG -->
| Profile | Route | Access | Select when | Avoid when |
| --- | --- | --- | --- | --- |
| `accessibility_engineer` | automatic | read-only | Accessibility conformance or assistive-technology behavior needs independent analysis. | General visual design or production UI implementation is the primary task. |
| `architect` | automatic | read-only | System boundaries, cross-cutting contracts, compatibility, or consequential design choices must be decided. | The design is already settled and the remaining work is bounded implementation. |
| `backend_dev` | manual | workspace-write | A bounded server, API, service, business-logic, or persistence change must be implemented. | The task is browser-only, mobile-only, infrastructure-only, or still needs root-cause discovery. |
| `build_verification` | automatic | read-only | Independent non-mutating build, test, packaging, installation, or release-readiness proof is required. | A failing check must be diagnosed or repaired. |
| `code_reviewer` | automatic | read-only | A completed or proposed change needs independent defect-focused review. | The primary need is implementation, planning, or broad repository discovery. |
| `data_engineer` | manual | workspace-write | Data movement, transformation, backfill, migration execution, or integrity validation must be implemented. | Only database schema design is needed, with no data-operation implementation. |
| `database_architect` | automatic | read-only | Schema, index, query-plan, migration, locking, or rollback design needs specialist review. | The approved design only needs migration or data-pipeline implementation. |
| `debugger` | manual | workspace-write | A failure must be reproduced and its root cause proven before a focused repair. | The desired behavior and implementation path are already known. |
| `devops_engineer` | manual | workspace-write | Infrastructure, delivery, deployment, runtime configuration, or operational automation must change. | The task is application implementation without delivery or runtime ownership. |
| `explorer` | automatic | read-only | Repository facts, execution paths, ownership, dependencies, or affected surfaces are not yet known. | The task requires design decisions or source changes. |
| `frontend_dev` | manual | workspace-write | A browser UI, component, client state, styling, or frontend test change must be implemented. | The task spans material server ownership or is only interaction design. |
| `fullstack_dev` | manual | workspace-write | One coherent change spans both browser-facing and server-facing contracts. | The work can be cleanly owned by one narrower frontend or backend specialist. |
| `general` | automatic | workspace-write | The work is bounded but no specialist profile has a justified capability match. | A narrower supported specialist clearly owns the task. |
| `mobile_dev` | manual | workspace-write | An iOS, Android, React Native, Flutter, or native mobile change must be implemented. | The task is browser web UI or a platform-neutral backend. |
| `performance_engineer` | automatic | read-only | Performance claims require measurement, profiling, bottleneck proof, or optimization-risk analysis. | The bottleneck is already proven and only an approved implementation remains. |
| `planner` | automatic | read-only | A decision-complete plan, dependency order, ownership map, or acceptance matrix is needed. | The task is a simple bounded execution step or requires editing project files. |
| `qa_engineer` | automatic | workspace-write | Acceptance coverage, regression tests, reproduction scenarios, or quality evidence must be created. | Only a non-mutating final command run or source-code review is needed. |
| `refactorer` | manual | workspace-write | The explicit goal is behavior-preserving structural improvement with regression proof. | New behavior, unresolved defects, or architecture decisions dominate the task. |
| `security_auditor` | automatic | read-only | Trust boundaries, authorization, secrets, crypto, dependencies, or protected data need defensive review. | The task is to implement a known security fix rather than audit it. |
| `technical_writer` | automatic | workspace-write | Verified behavior, architecture, commands, decisions, or ownership must be synchronized into durable docs. | Facts are unverified or production code changes are still required. |
| `ux_designer` | automatic | read-only | User flow, hierarchy, interaction states, responsive behavior, or implementation-ready UX rules are needed. | The design is settled and production frontend code must be written. |
<!-- END GENERATED PROFILE CATALOG -->

Routing is evidence-driven:

1. Use the canonical gate owner for planning, discovery, architecture,
   specialist audits, review, documentation, QA, and close.
2. For implementation, prefer the narrowest justified writer:
   `debugger` for reproduce-and-prove failures; `refactorer` for explicit
   behavior-preserving structure work; `frontend_dev`, `backend_dev`,
   `fullstack_dev`, or `mobile_dev` for their application surfaces;
   `data_engineer` for data movement and migration execution; and
   `devops_engineer` for infrastructure and delivery. Use `general` only when
   no specialist match is supported.
3. The coordinator owns the pipeline decision. Build or consciously accept the
   initial canonical pipeline, then follow the exact `pipeline.waves` snapshot
   returned by Cortex. Planner and explorer reports are advisory evidence, not
   commands to rebuild the pipeline. Change `future_waves` only when the
   coordinator concludes that verified evidence materially changes ownership,
   dependencies, risk gates, sequencing, or validation. Include a concise
   reason. Never restate or relabel an unchanged pipeline merely because a
   report completed.
4. A profile may own only its declared automatic gate, or—when it is a manual
   workspace writer—the implementation gate. Do not assign a writer to plan,
   discovery, review, audit, or close work. Do not assign a read-only analyst
   to implement a fix.
5. Each returned dispatch explains `phase`, `profile`, `capability`, `sandbox`,
   and `selection_reason`. Check that rationale against the latest evidence
   before invoking the dispatch. Never invent a role name or silently replace
   Cortex's native arguments.
   Do not treat a planned dispatch, commentary, or an empty wait as proof that
   a worker exists. The native call must return a child id before saying it was
   sent; retain those exact ids and wait only on them. A missing or failed
   native dispatch is a blocker, never permission to continue the wave.

Multiple workers with the same profile are separate bounded instances. Keep
their ownership, paths, dependencies, report refs, and native task identities
distinct. `profile` preserves the exact canonical role. `display_name` is the
human-readable `Profile Module` label (for example, `Explorer Auth`), and
`spawn_agent.task_name` is its host-safe task/attempt-unique key with an
ordinal and a
uniqueness digest. A new dispatch must use
`spawn_agent`; `followup_task` is reserved for resuming that exact native
worker after its durable question or other explicitly resumable pause. Cortex
rejects reuse of a `host_agent_id` already bound to another attempt. Since
dynamic host events report `agent_type=default`, lifecycle hooks use the
exact returned dispatch identity to bind each opaque child ID back to its
issued native task key and canonical profile before injecting worker context.
The hidden `spawn_agent` host contract currently exposes only `task_name` to
the native child list; it has no separate label field. Therefore the host may
render the unique key (for example, `architect_repository_08_<digest>`) even
though Cortex's `display_name` remains the human-readable `Architect
Repository` metadata and is used by lifecycle context. Never remove the key's
ordinal or digest to improve host rendering: that would reintroduce child
identity collisions. Explicit `visible_thread` dispatches use their
human-readable `display_name` as the native thread title.

## Repository knowledge consumption

The coordination-only root does not inspect project documentation itself.
Cortex automatically puts `docs/project/index.md` and
`docs/features/index.md` in every worker's Context files when they exist. The
first planning worker must read both before broad source discovery, use the
task goal/scope/paths to select all relevant linked project and feature pages,
and report those exact paths. When adapting future waves, the coordinator
copies that evidence-backed selection into each worker's `context_files`,
adding pages for cross-feature dependencies when later reports expose them.

Every downstream worker reads its supplied pages before broad search or edits
and re-checks the feature index so planner omission cannot silently hide an
affected capability. Documentation accelerates navigation and captures known
contracts, conventions, verification, decisions, and gotchas; it never
overrides current source, tests, schemas, migrations, or executable
configuration. Workers verify consequential claims and report stale pages,
contradictions, partial coverage, or missing links. Each persisted report must
contain one `Knowledge reviewed:` evidence entry naming both available indexes
and every additional page used; Cortex rejects a missing index acknowledgement.

Canonical phases are `plan`, `discover`, `architecture`,
`database_architecture`, `implementation`, `qa`, `security`, `performance`,
`accessibility`, `ux`, `review`, `documentation`, and `close`. A phase may
appear in only one wave; multiple valid owners for the same phase belong in
the same wave. `build_verification` is a profile and is also accepted as a
human alias for the final `close` phase. Generic `verification` maps to `qa`.
Cortex normalizes common aliases such as `implement`, but the coordinator
should use canonical phases from the returned snapshot rather than guessing.

## Relative one-call-per-wave workflow

Before step 1, distinguish host transport metadata from the user task. A
leading `<recommended_plugins>…</recommended_plugins>` block, an
`<environment_context>…</environment_context>` block, tool-usage directions,
and test-runner scaffolding are host metadata — never copy them into
`task.user_request`, objectives, labels, or worker prompts. When a caller
supplies an explicit `<cortex_task_contract>` block, its `user_request`,
acceptance criteria, and verification list are the complete task contract;
copy those values exactly and ignore text outside that block for task identity.
If no such block exists, preserve only the actual user-authored request after
removing the known host metadata wrappers. Do not retry `start_orchestration`
to repair a malformed request: construct the complete contract first, then
call it once.

1. Copy the exact user-authored task text into `task.user_request`; never
   paraphrase, normalize, summarize, or expand it. The sole host-metadata
   exception is Desktop's injected
   `[$cortex:orchestrator](absolute-local-plugin-path/skills/orchestrator/SKILL.md)`
   wrapper: remove that exact wrapper to `$cortex:orchestrator` before task
   identity, labels, persistence, and worker prompts. Preserve the selected
   route and every following user-authored word; arbitrary Markdown links and
   user paths remain unchanged. This prevents a local plugin-cache path or
   cache-version change from entering durable task state. Omit `task.objective` (if
   supplied for compatibility, Cortex requires it to match `user_request`
   exactly). Preserve material ambiguity: do not fabricate product intent,
   requirements, audience, design direction, behavior, or acceptance merely to
   make the dispatch look complete. Include success criteria, constraints,
   paths, approval boundaries, and user language only when supplied or already
   established. The coordinator owns the initial plan: it
   may supply compact waves or consciously accept Cortex's safe standard C2
   proposal. In either case, treat the returned `pipeline` snapshot as the
   authoritative current coordinator plan.
2. Call `start_orchestration` with exact absolute `project_root` and the task.
   Omit waves for the standard pipeline. A compact override is
   `{waves: [{workers: [{phase, depends_on, context_files, ...}]}]}`; only phase is required.
3. Invoke every returned `{worker, call, arguments}` exactly in one model turn
   when the host supports parallel tool calls. Spawned children run
   concurrently; correlate each `SubagentStart` by the exact returned
   `task_name`/`dispatch_ref` and host child id, never by a guessed ordinal or
   display label. If the host cannot batch calls, issue them in returned order
   as a transport fallback while preserving exact correlation. Also retain its
   sibling `dispatch_ref`, `briefing_path`, and `briefing_digest` as the issued
   immutable transport receipt. Native arguments are already filtered and the
   native message is intentionally a compact bootstrap, not the complete
   worker prompt. Do not read, inline, expand, or reconstruct the briefing in
   the coordinator, and never browse the surrounding Cortex ledger. Do not add IDs or turn expected model metadata into a
   native model override. Hidden `spawn_agent` dispatches must retain the
   returned `fork_turns: "none"`: the generated Cortex briefing is the
   complete worker context, and inheriting the coordinator transcript can leak
   localized user-language messages into the English-only worker channel.
4. Wait idly for the complete wave. Do not inspect or modify the project while
   any worker is active. Before any project action, each worker reads only the
   exact briefing path named by its bootstrap, verifies its read-only mode and
   SHA-256, and stops on a writable or mismatched artifact. If the host file
   reader alone cannot open that exact path, it calls `read_dispatch_briefing`
   with the complete bootstrap identity/digest tuple. If its bounded response
   is incomplete, it continues only with the returned cursor until complete.
   If the scoped read also fails, it stops with that diagnostic. This is
   its sole direct-read exception below `.codex/cortex`; it must never list or
   inspect the ledger, mutable state, baselines, delegation packages, another
   briefing, or report files. It records the exact bootstrap-provided
   `Dispatch briefing reviewed: <sha256>` marker in `report.evidence`;
   `record_report` revalidates both the marker and immutable file. Predecessor
   reports remain scoped tool reads. The fallback cannot list or select a
   different briefing and grants no general ledger access.
   A read-only worker chooses non-writing verification flags up front:
   `PYTHONDONTWRITEBYTECODE=1` for Python, no pytest/test/build cache, and no
   coverage or snapshot output. It skips a check that cannot be non-mutating
   and records the limitation; it never creates artifacts and then invokes
   `rm`, `git clean`, or a cleanup script. Cortex rejects newly changed
   generated or gitignored artifacts against that attempt's baseline.
   During this interval the coordinator is in `waiting_workers` with
   `output_policy="silent"`: repeated wait timeouts produce no heartbeat or
   status commentary. Visible output is limited to a worker question,
   completion/failure, or a blocking error.

   Any profile may first publish a material question with
   `worker_question(action="ask")`. The worker returns only its `question_ref`
   and a concise summary, publishes no report, and finishes its current native
   turn into an idle/resumable state; it must not busy-wait for the user.
   Call `manage_orchestration(intent="question", payload={"question_ref":
   "<exact ref>"})` exactly once. That call must open the host-native question
   UI; never restate the question as commentary or a final message, and never
   guess task, principal, thread, attempt, or profile identifiers. After the UI
   records the answer, use `followup_task` on the exact same native worker and
   instruct it to poll the same ref before resuming the same attempt. Do not
   dispatch a replacement or advance the wave. If native elicitation is
   unavailable, keep the durable question open and report the host limitation
   without asking the question as prose. Once
   complete, each worker publishes its strict eight-section
   `cortex/report/v1` through the scoped public `record_report` tool, then
   returns only `REPORT_RECORDED report_ref=<value>` plus at most a two-sentence
   summary. A worker must never paste the report JSON into the parent channel.
   When the dispatch supplies predecessor report refs, that successor worker
   first reads each ref through `read_worker_report` with the exact project
   root, task ref, attempt id, and profile from its briefing. It may not read
   any other report, publish the coordinator-only Markdown link, or call a
   lifecycle operation.
   `followup_task` resumes the same addressable native worker after a durable
   question answer or an explicit active steer. Active steer creates a task
   revision and sends the canonical English correction to the existing
   `host_agent_id`; it does not create an attempt or replacement worker. If a
   native worker finishes with a report-tool error or any
   acknowledgement other than `REPORT_RECORDED`/`QUESTION_RECORDED`, never
   follow it up directly: `SubagentStop` has already classified the attempt.
   Inspect once, then use the recovered report/question or submit the exact
   failed result so Cortex can issue a fresh authorized rework dispatch.
   Automatic rework is durable and bounded per phase: after three failed
   attempts Cortex blocks the task and creates a recovery handoff instead of
   spawning indefinitely. Use `manage_orchestration(intent="resume")` only
   after the reported cause is actually repaired; resume starts a fresh bounded
   recovery cycle.

   Every gate report must publish a separate top-level `gate_result` envelope with
   `decision`, `failure_class`, `findings`, `verification`, and `workspace`.
   It is canonical for all gates, including QA and implementation. The older
   top-level `closure` sibling remains only as a review/close compatibility
   alias and must never be nested inside the strict eight-field report; both
   forms must agree when supplied.
5. Read every returned ref with `read_worker_report`. The result includes the
   derived absolute `report_markdown_path` for the persisted
   `reports/markdown/<report-ref>.md` artifact. After each completed report,
   immediately publish the returned `report_markdown_link` verbatim as a
   compact clickable Markdown link in the main chat, before any other lifecycle
   call or additional report read. This is mandatory coordinator output, not
   optional metadata. The link supplements—not replaces—the concise summary
   and full report review. Never guess, substitute, or use the path to browse
   unrelated files. Then decide whether the coordinator-owned pipeline still fits, then call
   `continue_orchestration` once with `project_root`, the returned opaque
   `task_ref`, relative `step`, and results containing `report_ref`. A
   single-worker result may omit its slot; parallel results repeat the returned
   integer worker slot. A non-success result instead carries `status`, `reason`,
   and the exact `dispatch_ref` from
   that stopped worker's dispatch (or `context_handoff.stopped_workers`), with
   no `report_ref`; this binds recovery to one attempt. Inline report objects
   are compatibility-only and must not be requested from new workers.
6. Repeat until `outcome: completed`. If evidence changes future scope, send a
   compact `future_waves` replacement in the same continue call. Set
   `rework: true` only for intentional repetition of a completed phase.

### Recovery after context reset or compaction

When an automatic/manual compaction, a host `clear`, or a resumed context may have weakened
the active orchestration instructions, preserve the opaque `task_ref` and
call `manage_orchestration` with `intent="inspect"` exactly once. Treat the
returned `context_handoff` as the authoritative compact state and protocol
snapshot. It restores the goal, acceptance criteria, verified reports,
decisions, changed files, decisive checks, blockers, pipeline, and next
action from the durable ledger. It also separates `pending_dispatches` from
`active_workers`: invoke only the matching top-level inspect dispatches, never
spawn from the handoff itself, and wait on active workers only by their exact
persisted `host_agent_id`. The documented `SubagentStart` hook records the
native child id, actual model, and exact returned dispatch identity;
dynamic workers report generic `agent_type=default`. A running
attempt without that binding fails closed rather than being respawned or
waited on with an empty target. Never call `start_orchestration` again,
replay completed dispatches, or rely on a raw transcript. After rehydration,
continue the existing relative step and publish every returned exact
`report_markdown_link` before any other lifecycle or report-read call.

### Active steer and correcting a completed task

For an active or blocked task, a user correction is an active steer through
`manage_orchestration(intent="steer")` (aliases `amend` and
`revise_active_task`) with `payload.user_message`. The coordinator supplies
canonical English `message_en` when the user's message is localized. Cortex
creates a task revision, stores both message forms, computes a bounded impact
summary, and returns `followup_task` calls only for existing addressable native
workers. A missing `host_agent_id` leaves the steer durable; do not guess a
resume target or spawn a replacement merely to deliver it. Use `follow_up`
only for a completed source task.

### Correcting a completed task

Never silently reopen or alter a task that has reached `outcome: completed`.
When the user asks to correct its result, preserve the user's new wording and
call `manage_orchestration` with the completed source `task_ref`,
`intent="follow_up"`, and `payload.user_request` set to that exact corrective
request. The payload may select a bounded `report_refs` list and normal task
fields such as `complexity`, `scope`, or `verification`. Cortex creates a new,
linked corrective task and returns its own `task_ref` and first dispatches.
The original task remains immutable; the new Planner receives derived source
handoff and report Markdown paths as historical evidence. Do not execute the
source task again, or treat the link as proof that its old findings still match
the repository. For an active task that has not completed, use normal
evidence-based `rework` instead; `follow_up` rejects it.

### Required post-plan approval

The public task field `task.plan_approval` accepts `auto` or `required`. It
defaults to `required` for C2/C3 and `auto` for C1; `auto` does not make user
confirmation mandatory. Harvest routes force `auto` regardless of complexity.
When policy is `required`, the plan phase must be in
its own wave. After a successful plan wave, Cortex returns
`outcome: awaiting_plan_approval`, sends no successor dispatch, and provides
`plan_review` with the planner `report_ref`, derived absolute
`report_markdown_path`, `summary`, `findings`,
`uncertainty`, `next_action`, and `remaining_phases`. Read the referenced
report, present a concise plan summary in the main chat, and wait for the
user's explicit decision. To continue, call
`manage_orchestration(intent="plan_approval", payload={"decision":"approve"})`;
this dispatches the next wave. To request changes, call
`manage_orchestration(intent="plan_approval", payload={"decision":"revise", "feedback":"..."})`
with non-empty feedback; Cortex reruns the Planner and presents a new review.
Do not turn this into a second worker-question flow: material questions remain
distinct and are resolved through `worker_question` during planning.

The Planner's same-call public `record_report` may include a separate
`planning` object with exactly `overview` and `work_packages`; the strict
eight-field `cortex/report/v1` report is unchanged. Each package requires
`id`, `title`, `objective`, and non-empty `microtasks`, with optional
`allowed_paths` and `depends_on`. Each microtask requires `id`, `title`, and
`objective`, with optional `profile`, `allowed_paths`, `depends_on`,
`acceptance_criteria`, and `verification`. Use explicit ownership and
dependencies; Cortex validates package and per-package microtask DAGs and
enforces limits of 32 packages, 32 microtasks per package, and 128 total.
Remain read-only: Cortex—not the Planner—materializes
`.codex/cortex/tasks/<task>/planning/manifest.json`, `overview.md`, and
immutable `revisions/plan-<report-ref>/packages/<id>.json` artifacts. The
manifest is the current pointer/source of truth and revisions preserve prior
approved or revised plans. `plan_review.planning_artifacts` is the compact
approval-review projection. This durable catalog supports
ownership/dependency-aware scheduling; it does not authorize an unconstrained
auto-executor beyond the canonical phase/wave safety model.

Normal flow uses no caller-generated submission/task/wave/attempt IDs, no
coordinator identity, and no echoed host tool/model/effort. A relative `step`
is required only to separate retries from an identical report used on a later
wave. Preserve and echo the opaque `task_ref` returned by Cortex so concurrent
tasks in the same project remain isolated. Durable task IDs, receipts,
evidence, verification, manifest, and handoff stay private in the canonical
v8 ledger. Legacy v7/v3 state is unsupported and is never created, migrated,
or resumed.

When several tasks are active Cortex returns `needs_selection` with objective
and opaque `task_ref`; use the matching ref on every subsequent lifecycle and
report-read call. Use `manage_orchestration` for inspect, resume, deactivate,
lane, resource, active `steer`, or a durable MCP UI question. Different task contracts may run
concurrently in one project; an exact duplicate start remains an idempotent
replay of the existing active task.

`start_orchestration` is called exactly once per task contract. Its response
contains `replayed`. A fresh start returns the only authorized dispatches; a
replay returns no dispatches and must never launch a second wave. If the
original response was lost before dispatch, use `manage_orchestration` inspect
once and invoke only its still-awaiting recovery dispatches. Preparing native
arguments, translating commentary, or recovering an acknowledgement is not a
reason to restart.

If a recovery operation reports that the coordinator was deactivated, do not
copy its internal activation diagnostic into the user response. Keep the
current task reference, use the Cortex skill route again when the user has
explicitly selected it, and retry the lifecycle operation through
`manage_orchestration`/`continue_orchestration`. For a linked `follow_up`, an
idempotent replay may restore the server-owned activation and must be handled
as the existing corrective task; do not create a duplicate or resume the
completed source task.

## Model and dispatch contract

Apply the adaptive model policy defined in `profiles.json`. `explorer` always
selects Luna, using coordinator-selected effort or the risk-based default;
Terra is reserved for a hidden host-unavailable fallback. Security context,
the security gate, and `security_auditor` always select Sol, with effort floors
C1 `medium`, C2 `high`, and C3 `xhigh`. Ordinary profiles are divided into
efficient, adaptive, and deep classes. Efficient work uses Luna; deep profiles
use Terra, as do C2/C3 planning and `terra_task_kinds` entries (including
uncertain diagnosis, long-context, and integration-conflict work), plus
high/critical failure cost. Other low/moderate-risk adaptive work stays on
Luna. Efficient Luna uses
C1/C2/C3 `high`/`high`/`xhigh`; bounded adaptive Luna uses
`high`/`xhigh`/`max`; Terra uses `high`/`high`/`xhigh`. Risk floors remain
low/moderate `medium`, high `high`, critical `xhigh`. The complete effort
vocabulary is `low`, `medium`, `high`, `xhigh`, and `max`; automatic `max` is
limited to bounded C3 Luna work. A coordinator may
explicitly override an ordinary route between Luna and Terra, but cannot lower
its effort floor.

Non-security Sol is valid only when the user explicitly chose it. Set the
compact worker's `user_requested_model: sol`; omit `model` or also set it to
`sol`. Cortex records matching `user_requested_model` and `requested_model`.
Coordinator preference, an earlier Terra failure, and auditable-extreme labels do not grant
Sol. Do not use the retired `sol_escalation` field or any model/effort remap.
Configured-default Luna dispatches omit native `model` while preserving
reasoning effort. Explicit model selections retain `model`; if Luna is not
available to the host, a hidden Terra fallback preserves the selected effort.
Expected routing is not host attestation; claim the actual model only from host
runtime metadata.

Workers remain internal, English-only, bounded to ownership and allowed paths,
and cannot subdelegate without explicit authorization. Emit English in every
worker message, tool argument, report, durable question, handoff, and native
final response; treat non-English task text as input data. The original user
language belongs to the main coordinator, which alone communicates with and
localizes for the user. For durable worker questions, keep the ledger content
English and use only the coordinator's `localized_question`,
`localized_header`, `localized_options`, and `localized_custom_label` fields
for transient user-language UI projections. Answers retain original
language/value and require canonical English `answer_en` for localized free
text before the worker resumes. Workers may use
`worker_question(action="ask_batch")` with 1–32 stable questions and poll the
same `batch_ref` with `action="poll_batch"`; the host form answers every item
in one atomic batch. A task revision supersedes an unresolved batch rather
than resuming stale user intent. A `follow_up` task inherits the
completed source task's user language while preserving this English-only
worker boundary.

When Codebase Memory tools are present, generated worker briefings require an
exact-root `list_projects` match and prefer indexed architecture, graph search,
call/data-flow tracing, and impact analysis before broad filesystem search.
Workers confirm consequential indexed facts in current source or tests.
Planner, explorer, architect, and database architect may refresh one missing or
stale index; other profiles use a bounded fallback without setup loops. This is
worker project work: the root coordinator must never call Codebase Memory
itself.

## Reports, questions, and completion

Cortex captures the initial and every per-attempt file manifest as an
immutable, content-addressed `cortex.db` record.
Task and attempt state carry only compact `manifest-<sha256>` refs. Equal
project state deduplicates, but every dispatch performs a fresh capture so
external changes are still detected. After terminal completion is persisted,
Cortex removes those database manifest records;
final receipts retain digest and changed-file proof. An explicit
`allow_rework` reopening establishes a fresh active baseline before new work.

Every persisted report contains exactly `summary`, `findings`, `questions`,
`changed_files`, `tests`, `evidence`, `uncertainty`, and `next_action`.
The final `questions` list is always empty: material questions use the durable
`worker_question` lifecycle before report publication, while genuinely
non-blocking evidence limitations belong in `uncertainty`. Cortex rejects a
report that uses `questions` as an escape hatch.
`record_report` returns a compact `report_ref`; `read_worker_report` is the
coordinator's bounded read path and returns the derived absolute
`report_markdown_path` for the persisted Markdown artifact. After reading each
completed report, publish a compact clickable Markdown link in the main chat
using that exact returned path, in addition to the concise summary and report
review. Never guess or substitute the path, or use it to browse unrelated
files. A successful native worker response contains
only that ref and a short summary. Non-success returns only a normalized status
and reason. Cortex validates all parallel slots and report refs before gate
state writes and preserves quotas, redaction, one-use receipts,
documentation/close, rework invalidation, and manifest-backed handoff. If a
native worker is interrupted after persisting but before returning its ack,
use `manage_orchestration` inspect to recover `available_reports`, which also
includes the derived report path; do not ask
the worker to regenerate a large inline report.

The public dispatch result is deliberately compact: it carries a
`dispatch_ref`, immutable `briefing_path`, `briefing_digest`, and a short native
bootstrap. The full worker prompt is absent from the MCP result and mutable
coordination state. Workers may directly read only their issued
briefing; a failed host file read has one identity-and-digest-scoped
`read_dispatch_briefing` fallback. All reports, questions, and lifecycle state stay behind scoped Cortex
tools. This prevents output-size truncation without granting general ledger
filesystem access.

When a dispatch contains predecessor handoffs, the worker must review every
one before project work and include the generated `Predecessor review:` entry
in report evidence. `record_report` rejects missing acknowledgements. Omitted
`depends_on` means all verified predecessor reports; an explicit phase list
selects only those dependencies. Cortex fails closed instead of silently
dropping reports when the count or context budget would be exceeded.

Questions are a general worker lifecycle, not a Planner-only report field.
Every worker distinguishes repository-resolvable facts, low-impact reversible
choices, and material user decisions. Only the last category uses
`worker_question`; never convert it into an assumption. Existing code describes
the current system, not the user's desired outcome. The durable ref returns
through the native parent channel; call `manage_orchestration` with intent
`question` and that ref so Cortex projects it through MCP
`elicitation/create`. After the answer, resume the same worker and let it poll.
Open blocking questions make `record_report` and `continue_orchestration` fail
closed. Never answer on the user's behalf if the host cannot render
elicitation.

Finish only after `outcome` is `completed` and report the verified handoff and
any live-evaluation limitations plainly.
