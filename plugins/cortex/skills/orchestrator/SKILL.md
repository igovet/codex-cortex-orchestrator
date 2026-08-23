---
name: orchestrator
description: Explicit opt-in Cortex coordinator. Use only when the user directly selects or mentions cortex:orchestrator. Never activate from task complexity alone.
---

# Cortex Orchestrator

## Invocation and routes

In Codex Desktop use the Skills picker to select `cortex:orchestrator` or
mention `$cortex:orchestrator`. In CLI use `$cortex:orchestrator` or `/skills`.
Bare `/cortex` and `/normal` are textual shorthand, not registered native slash
commands. Do not use obsolete prompt aliases.

Never present a bare `/cortex` or `/normal` token as a required next step or
ask the user to send it as a recovery command. Those tokens are not native
host commands. If activation is needed, use the Skills picker to select
`cortex:orchestrator` or mention `$cortex:orchestrator`; to leave the route,
use `$cortex:orchestrator normal`. A Cortex lifecycle response that says a
task is complete is terminal: result the verified handoff and limitations
without asking for another activation.

| Exact argument | Route | Effect |
| --- | --- | --- |
| `empty` | `orchestrate` | Start normal relative orchestration. |
| `help` | `help` | Explain Cortex without writes. |
| `harvest` | `harvest` | Incrementally synchronize knowledge docs. |
| `harvest-refresh` | `harvest-refresh` | Fully re-audit knowledge docs. |
| `prune` | `prune` | Remove only completed host-private Cortex task state stale for at least seven days. |
| `normal` | `normal` | Exit the active Cortex session. |

Do not guess unknown arguments. Show help and ask the user to choose.

The help route explains invocation, opt-in behavior, the host-private Cortex
ledger, and the nine-operation public registry. The registry contains the five
coordinator operations and the five strict worker operations, with one shared
operation between the role projections. A host may expose the strict
five-operation `worker` or `coordinator` projection when it can establish
role-specific capabilities; exposure does not change server-side scope checks.
Help performs no activation, dispatch, or write. Source/tests outrank generated
docs.

The empty, `harvest`, and `harvest-refresh` routes explicitly authorize durable
orchestration; `prune` authorizes only the bounded maintenance call below.
Ordinary work never activates Cortex. The normal route uses
`manage_orchestration` with intent `deactivate` only when a Cortex task is
active.

For every activated orchestration or harvest route, read and apply
`../cortex-control/SKILL.md` before the first lifecycle call. That bundled
skill is the authoritative runtime protocol for root isolation, dispatch,
questions, evidence, recovery, ownership, verification, and private diagnostic
handling. No project-local `AGENTS.md` is part of the installed contract.

## Turn-local read discipline

Maintain a turn-local evidence index of every fully read skill, file, and
bounded source range. Read each exact path only once per coordinator turn and
reuse that evidence; never reload the Orchestrator skill, Cortex Control skill,
or an unchanged project file just because a lifecycle response needs attention.
Read again only after an explicit truncation/pagination, a post-read file edit,
or when a distinct unread range is necessary. Search before opening a large
file and read only the required range. This does not relax the root's ban on
project, plugin, cache, or ledger inspection during active orchestration.

The `prune` route is maintenance, not a coding pipeline. After explicit user
selection, call `manage_orchestration` once with exact absolute `project_root`,
intent `prune`, no `task_ref`, and
`payload: {"confirmation":"PRUNE","older_than_days":7}`. It removes only
completed task-scoped host-private Cortex state last updated at least seven
days ago, and reconciles task indexes, public starts,
activations, operation receipts, classification receipts, task resource
claims, and lane bindings. It preserves every active or blocked task regardless
of age and never removes a classification receipt referenced by a retained
task. It also preserves recent completed tasks, lanes, source, documentation,
and plugin files. Never reinterpret `prune` as clear-all. When no retention
period is supplied, the route presents the stable choices `keep_1d`, `keep_7d`,
`keep_30d`, and `full_reset`. The first three map to bounded retention
windows. `full_reset` is separately destructive: it requires the exact second
confirmation `RESET CORTEX`, refuses to run while any task is active, and
removes only host-private Cortex state while preserving project source and docs.

## Harvest route contract

For exact `harvest` or `harvest-refresh`, read
`../knowledge-harvest/SKILL.md` and its linked
`references/feature-census.md` completely before calling Cortex. Those files
define the mandatory inventory, coverage matrix, feature-page depth, and
completeness gates. Do not substitute a generic documentation task.

Both routes start with the canonical phases `scope`, `discover`, `architecture`,
`plan`, `documentation`, `review`, and `close`. Planner Scope first publishes a
discovery brief, relevant context, and all validated non-overlapping domains; the
final Planner consumes all predecessor result projections after architecture.
After reading the scoping projection, the
coordinator must decide whether the repository is large enough to split the
single discovery placeholder into 2–8 parallel `explorer` workers with
non-overlapping domain ownership. A repository with several applications,
services, packages, runtime processes, or integration families is large for
this purpose. Each discovery worker declares `depends_on: ["scope"]`. After the
domain census, the architecture worker receives `scope` and `discover`; each
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
are required. Any gap triggers unbounded documentation rework until it is
resolved or an explicit non-retryable blocker is recorded, rather than a
successful close.

The coordinator must reject semantic results that lack inventory counts,
domain/source coverage, mapping/exclusion evidence, and concrete coverage gaps.
A
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
next implementation step appears obvious from a worker result.

The coordinator may only clarify the user's goal, call the public Cortex
lifecycle and result-read tools, invoke the exact returned native dispatches,
wait for workers, relay questions, evaluate results and gate evidence, adjust
future waves through Cortex, and communicate the integrated result to the user. All project
operations, including follow-up inspection and implementation after a planning
result, belong to a dispatched worker. The coordinator must remain idle while
a worker is active. Never work in parallel with an active
worker or substitute coordinator work for a missing, slow, failed, or blocked
worker. If dispatch is unavailable, keep the task resumable and route the
condition through server-owned recovery or one concrete task question; never
stop Cortex or fall back to direct project work.

Technical lifecycle failures are never user-facing blockers. Invalidated
attempts, validation errors, stale receipts, replay-registry drift, failed
dispatches, and contradictory internal projections must be recorded as JSONC
recovery evidence and repaired by Cortex through the same attempt or a
server-owned corrective dispatch. The coordinator must not answer that Cortex
is blocked and must not loop through `manage_orchestration` repeatedly. For a
completed worker result, read it once, call `continue_orchestration` once with
the exact server continuation, and follow the returned dispatch or wait. Only
a durable worker question or explicit plan approval can stop the ordinary
chat.

## Team intelligence and routing

The main/root agent is the user-facing mission commander and integration
authority. It owns goal clarification, routing, wave decisions, evidence
gates, recovery, and the final answer. It is never a hidden implementer.
Workers are internal specialists with bounded ownership; they result to the
main coordinator and never become an alternate user-facing authority.

Workers publish semantic AttemptResult facts and, when useful, incremental
AttemptEvents. Cortex owns attempt identity, receipts, workspace observations,
checks, timestamps, and evidence markers; it exposes result refs and scoped
human/handoff projections consumed by the coordinator. A projection is a
handoff view, not the worker's authoritative transport.

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
| `planner` | automatic | read-only | Early discovery domains or a decision-complete final plan, dependency order, ownership map, or acceptance matrix is needed. | The task is a simple bounded execution step or requires editing project files. |
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
   returned by Cortex. Planner and explorer results are advisory evidence, not
   commands to rebuild the pipeline. Change `future_waves` only when the
   coordinator concludes that verified evidence materially changes ownership,
   dependencies, risk gates, sequencing, or validation. Include a concise
   reason. Never restate or relabel an unchanged pipeline merely because a
   result completed. Context narrowing changes dependencies, not phase
   membership: Cortex rejects removal of a pending implementation obligation
   and automatically infers rework when a replacement repeats a current or
   completed phase. If an accepted implementation plan reaches documentation
   or close without implementation and its required QA/audit/review evidence,
   Cortex restores a Planner-first full delivery graph before dispatch; never
   substitute another documentation worker. Evidence-backed material replans
   have no task-lifetime quota: `replan_count` is audit history and the prior
   `replan_limit` field cannot terminate a progressing task. Cortex preflights
   the complete replacement before mutating the current gate. If an older
   failed replan left an active gate with no live or pending dispatch, recover
   it with one Planner-first resume payload rather than creating a follow-up.
4. A profile may own only its declared automatic gate, or—when it is a manual
   workspace writer—the implementation gate. Do not assign a writer to plan,
   discovery, review, audit, or close work. Do not assign a read-only analyst
   to implement a fix.
5. Each returned dispatch explains `phase`, `profile`, `capability`, `sandbox`,
   and `selection_reason`. Check that rationale against the latest evidence
   before invoking the dispatch. Never invent a role name or silently replace
   Cortex's native arguments. A `ready_to_spawn` response authorizes only its
   returned `dispatch.call` with those exact `dispatch.arguments`: a generic
   collaboration spawn, self-authored task name, or replacement child cannot
   bind to or advance the issued Cortex attempt.
   Do not treat a planned dispatch, commentary, or an empty wait as proof that
   a worker exists. The native call must return a child id before saying it was
   sent; retain those exact ids and wait only on them. A missing or failed
   native dispatch is internal recovery evidence: let Cortex derive one
   corrective dispatch and never expose it as a blocker or continue the wave
   from an unbound child.

Multiple workers with the same profile are separate bounded instances. Keep
their ownership, paths, dependencies, result refs, and native task identities
distinct. `profile` preserves the exact canonical role. `display_name` is the
human-readable `Profile Module` label (for example, `Explorer Auth`), and
`spawn_agent.task_name` is its host-safe task/attempt-unique key with an
ordinal and a
uniqueness digest. A new dispatch must use
`spawn_agent`; `followup_task` is reserved for resuming that exact native
worker after its durable question or other explicitly resumable pause. Cortex
rejects reuse of a `host_agent_id` already bound to another attempt. Since
dynamic host events result `agent_type=default`, lifecycle hooks use the
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

## Runtime protocol handoff

### User communication

User-visible updates use the communication contract in `profiles.json`. The
default `natural` profile favors plain language; `compact` limits updates to
the essential result and next step; `technical` retains useful implementation
detail. Select with `communication_profile` or `CORTEX_COMMUNICATION_PROFILE`;
unknown values fall back to `natural`. Human-readable message types are kept
separate from internal transport metadata. Before publication, check plain
language, absence of internal identifiers and tool names, non-repetition, an
explicit next step, and the selected profile's length/detail expectations.

Natural-facing output is a strict presentation boundary. In the default
`natural` profile, every user-visible update contains only 3–5 short steps:
what happened, why it matters, what happens next, and—when applicable—the one
decision required from the user. Internal protocol names, lifecycle states,
worker identities, dispatch references, cursors, result refs, model names,
and validation implementation details stay internal. Ask at most one clear
question in a user-facing message. If all material uncertainty is closed and
the plan is executable, the coordinator must recommend **Approve** explicitly;
it may recommend **Revise** only when a concrete unresolved risk, dependency,
or verification gap remains. Waiting for workers produces no user-facing
heartbeat or progress message.

For an activated route, `../cortex-control/SKILL.md` is the single coordinator
core and state-machine authority. Load it completely before the first lifecycle
call and follow its exact tool sequence, silent-wait policy, question flow,
result processing, unbounded rework escalation, steer/follow-up distinction, model routing,
recovery, and completion contract. Do not restate or reinterpret that protocol
here.

Before every Cortex lifecycle or recovery tool call, use the exact nested JSON
schema advertised for that tool by the active MCP `tools/list` surface. Never
infer field names, enum values, or nested paths from prose, a prior error, or
the transcript; apply all returned field diagnostics atomically to the same
request and preserve fields that already passed validation.

Before `start_orchestration`, ordinary tasks have non-empty
`task.acceptance_criteria` and `task.verification` grounded in the exact user
request or verified authority. Ask the user first when a material criterion
cannot be derived without inventing intent. Planner work packages keep
`profile` forbidden at package level; each microtask has non-empty
`verification`, explicit `profile`, narrow non-broad `allowed_paths`, and
non-empty acceptance criteria. Cortex compiles the approved dependency graph
into an immutable executable plan unit instead of dispatching a generic
implementation mission.

After compaction or uncertain host state, use the Cortex Control recovery rule:
inspect once, reconcile `pending_dispatches` and `active_workers`, and never
reconstruct lifecycle state from chat. The coordinator must remain idle while a
worker is active and may never use patch, shell, or project-inspection tools as
a fallback.

Native worker-slot cleanup, all wait behavior, result-link publication,
`next_strategy` retry handling, and terminal completion are defined only in
Cortex Control. A native spawn, wait, child message, or local child close is
never completion evidence: read the exact canonical AttemptResult and wait for
the server-derived `continue_orchestration` continuation/terminal audit before
presenting any result. A completed lifecycle response is terminal: present the
evidence-backed result and every unrun release gate without asking the user to
activate Cortex again.
