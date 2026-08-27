# Architecture decisions

<!-- GENERATED:START -->

## Durable ledger, not workflow engine

V12 deliberately separates model judgment from backend persistence. The model
owns the orchestration DAG, delegation, evidence interpretation, governance
depth, verification strategy, initiatives, rework, interaction holds, genuine
user questions, and closure. It never authors a project solution plan: a
planner worker owns that decomposition and its immutable plan report. The
backend owns durable IDs, strict schemas,
transactions, idempotency, reference integrity, ordered history, bounded report
assembly/read recovery, and project isolation.

No server-owned waves, gates, mandatory ordering, plan authority, lifecycle
capabilities, receipts, repair routes, closure breakers, or recovery escalation
remain in the active V12 contract.

The coordinator maintains and persists only a model-owned DAG of optional
worker-owned stages through existing task-linked initiative revisions and the
delegation/report/decision graph: discovery, planning, implementation, review,
verification, security, documentation-impact, documentation sync/verification,
knowledge harvest, and closure evidence. Nodes state their ownership,
predecessors, expected evidence,
and `proposed`, `waiting_predecessors`, `ready`, `dispatched`,
`evidence_received`, `rework_required`, `cancelled`, or `settled` status. This
is advisory pipeline history, not backend lifecycle state. A planner `plan` report
is the explicit predecessor of plan-dependent nodes. New worker evidence may
add, remove, reorder, retry, or parent-link rework nodes while preserving all
completed report evidence.

## Strict coordinator/worker separation

The root coordinator is permanently orchestration-only. It may define the
outcome, constraints, acceptance criteria, and verification needs; choose or
revise governance; create/inspect ledger tasks and delegations; select exact
worker model/effort; coordinate native workers; read reports; choose rework or
replacement; record advisory closure; and synthesize the final answer.

It may not inspect, search, read, create, or edit target-project content,
perform substantive domain analysis, or run project commands, builds, tests,
browser actions, or direct verification. Discovery, planning grounded in
project state, implementation, documentation work, review, and verification
are worker-owned. A missing or contradictory report produces another bounded
delegation rather than coordinator-side investigation.

The only project-read exception is bounded knowledge routing. Before project
delegation, the coordinator must read applicable `AGENTS.md`, the project and
feature indexes, and only
task-relevant pages selected from those indexes so it can compile the
delegation's knowledge requirements. It may not use that exception for project
discovery or domain analysis. The bundled orchestrator skill alone owns the
exact route and six-part template; profiles consume the supplied contract and
never reconstruct routing.

The exception is closed to non-shell direct reads of already-known exact paths.
It grants no shell/command, `rg`, `find`, glob, graph/source/repository-search,
directory-listing, or candidate-path authority. Unknown project roots or
document paths require a discovery/retrieval worker. Root discovery and every
project-local artifact or state check—including Git, manifests, caches,
worktrees, existence/absence or unchanged-state, and `.codex`—are also
worker-owned, regardless of read-only, plan, report-recovery, or direct user
framing.

## Uniform eleven-tool facade

The facade exposes exactly eleven action-specific tools and one input schema for
every participant. There is no coordinator/worker projection or audience-based
field filtering. `tools/list` is the authoritative registry, and runtime
validation consumes the same schema objects.

The catalog is intentionally small: six core coordination operations, four
advisory-governance operations, and `record_user_decision` for ordinary-chat
evidence. Native agent creation, waiting, filesystem changes, permissions, and
external actions remain outside the ledger.

## Durable task and report evidence

A task is the project-scoped root of delegations, reports, and decisions. Before
the first project delegation, it records the versioned task/result contract:
the exact arbitrary-Unicode `user_request_original`, `user_language`, English
normalized `objective`, `requirements`, `constraints`, `acceptance_criteria`,
and `verification_plan`. This contract preserves intent and acceptance context;
it is neither an implementation plan nor a backend permission boundary.

A delegation captures bounded objective, advisory role, scope, instructions,
parent, input report/decision IDs, and required exact model/effort metadata. It
creates no host authority. Its `instructions` contain the coordinator-compiled
six-part knowledge contract. The orchestrator alone owns compilation; profiles
consume it and may not rebuild routing.

Only `create_task` accepts explicit resolved `project_root` and binds its stable
`task_id` to that canonical project ledger. Every other public operation
requires the task anchor and derives or validates the project from the saved
task. Initiative operations use it only as a project locator, never as
permission. No guaranteed project root exists in MCP call metadata; the plugin
process working directory is the plugin, not the target project, and V12
intentionally has no root-binding hook. The native worker brief carries the
saved root for working-directory context. Optional task `context` remains
arbitrary JSON. Delegation `scope` is required non-empty text (up to 65,536
characters) describing the concise worker-ownership boundary; detailed
execution belongs in `instructions`, and object scope is invalid. Governance
closure names its existing task or initiative through required `subject_type`
and `subject_id`.

Reports are immutable `progress`, `result`, or `synthesis` evidence with
`partial`, `completed`, `blocked`, or `failed` status; `plan` is the fourth
report type. A plan's review policy is `informational` or `required`.
`required` means the coordinator presents the exact finalized plan/digest,
explains the review, requests an unambiguous approve/revise/cancel response,
and ends the turn. It is ordinary-chat model policy, not a backend gate.

Reports use one immutable lifecycle: `single`, or `begin` followed by sequential
`append` calls and `finalize`, or `begin` followed by `abort`. A single report is
limited to 65,536 bytes; an appended chunk to 32,768 bytes; a report to 256
chunks and 8 MiB. A failed or interrupted assembly resumes from its stored
manifest and next index. It is never restarted at zero, overwritten, or treated
as finalized; a replacement explicitly supersedes its predecessor. Report IDs
can be passed to later delegations. Reading creates no receipt; report status is
not backend acceptance or native lifecycle evidence.

`read_reports` preserves the requested order for at most 20 unique known IDs
and is the only report body/chunk reader. It accepts an optional named-section
selection, returns no more than 65,536 content bytes per call, and supplies a
scope-bound cursor for exact resumption; `max_bytes=0` returns metadata only.
Task, delegation, and governance inspection use `after_sequence` plus `limit`,
return compact report references, and expose `timeline`, `next_sequence`, and
`has_more` rather than opaque capability cursors.

`record_user_decision` appends a coordinator-asserted ordinary-chat decision
against an existing task, delegation, plan, report, or same-project initiative.
It retains `prompt_en`, exact `response_original`, English `response_en`, user
language, subject identity, immutable plan/report digest where applicable, and
optional supersession. The recorded `user_via_coordinator` attribution is
durable evidence only: the backend verifies scope and binding, but does not
authenticate the user or grant authorization. A revision receives a new plan
and digest; silence and unrelated text are not approval.

## Model-owned governance

C1/C2/C3 are retained as advisory planning baselines: C1 is bounded low-risk
work with normally `minimal` governance; C2 is multi-step or cross-surface work
with normally `light`; C3 is high-risk/cross-domain work with normally `full`.
Security/privacy, migration, public contracts, concurrency, destructive,
production-critical, and long-lived work are C3 signals. Evidence and an
explicit user preference may revise either label or depth. Neither produces a
backend wave, mandatory stage, automatic model escalation, or user gate.

`minimal`, `light`, and `full` describe proportional reasoning depth. The model
selects and revises the mode; an explicit user override is stored with priority.
The backend never infers complexity or automatically promotes a task.

Assessments append rather than update in place. With no user override, the
latest model assessment forms the effective projection. Once present, the
latest user override remains effective across later model assessments; those
rows preserve warnings and revised recommendations without silently replacing
the user's choice. Earlier statements remain available for audit, and there is
no stale-revision rejection.

Closure verdicts—`ready`, `ready_with_risks`, and `not_ready`—are recommendations
from the model. Missing closure or `not_ready` may influence the explanation but
cannot prevent rework, report access, or a final answer.

## Project-level initiatives

Initiatives are project objects that may outlive a task and link several tasks,
reports, parent initiatives, and dependencies. A current initiative row provides
the projection; each change appends an initiative revision with its immutable
payload. Task-anchored governance inspection returns only initiatives and links
related to that task unless a specific related initiative is selected.

The status vocabulary is bounded, but transitions are intentionally free. The
backend does not compute completion or reject a closure because work or a
dependency remains open. Missing and cyclic dependencies remain warnings so the
model can account for them.

## Exact model transport

Profile/model/effort selection belongs to the coordinator. Agent profiles are
advisory prompt templates and cannot authorize or reject a delegation.

Luna, Terra, and Sol each support `low`, `medium`, `high`, `xhigh`, and `max`;
their canonical recommended effort is `high`. Native dispatches use
`fork_turns="none"` and carry the exact selected effort. Luna is the configured
default, so logical Luna omits the native `model` argument. Terra and Sol are
explicit overrides.

There is no server-owned fallback ladder. A replacement worker receives a fresh
model/effort decision from the coordinator.

## Project-isolated schema v1

V12 creates a fresh database family under
`~/.codex/cortex/v12/projects/p-<project-hash>/cortex.db`. The schema begins at
version 1 and includes a database-family application ID plus project metadata.
It is not an upgrade of V11.

Every mutation can use an idempotency key. Same key and normalized payload
replay the original result; a conflicting payload returns a non-mutating
conflict. SQLite write transactions serialize concurrent revisions and keep
timeline order atomic.

## Canonical database and derived human views

The host-private SQLite database is canonical. It may materialize a task's
derived Markdown views beside the project shard, in this exact layout:

```text
~/.codex/cortex/v12/projects/p-<hash>/
└── tasks/<task_ref>/
    ├── index.md
    ├── task.md
    ├── plans/current.md
    ├── plans/revisions/<plan-report-id>.md
    ├── delegations/<delegation-id>.md
    ├── reports/<report-id>.md
    ├── decisions/<decision-id>.md
    ├── initiatives/<initiative-id>.md
    ├── closures/<closure-id>.md
    ├── governance-gate.md
    ├── handoffs/report-consumption-receipts.md
    └── timeline/
        ├── index.md
        └── pages/<first-sequence>-<last-sequence>.md
```

These files are disposable views, never ledger authority, recovery inputs, or
native worker instructions. Cortex performs zero database, report, decision,
projection, or `.codex` writes below `project_root`. A current view is publishable
only as `ready`, after contained regular-file, digest, and source-sequence
verification. `stale`, `conflict`, `unavailable`, and `disabled` produce no
clickable path. A coordinator publishes a returned `ready` absolute path only
with a localized summary and effect/next step; failure to provide a view never
blocks canonical evidence or a final answer.

## No hooks or server recovery

Lifecycle hooks are absent from V12. Host start/stop observations, native wait
output, environment state, and session identity are neither stored authority nor
completion evidence. The server does not reconstruct workers, launch
replacements, or choose a recovery route.

Worker loss or missing evidence is interpreted by the coordinator, which may
create a replacement delegation or disclose the limitation. This decision
requires no backend lifecycle ceremony.

## Operator maintenance stays outside MCP

Health, project-shard backup, checkpoint, optimize, vacuum, offline restore,
projection prune/regeneration, and sealed-backup retention are explicit local
administrator actions in `cortex_runtime.v12_maintenance`. They do not add an
MCP tool, timeline lifecycle, governance permission, or worker capability. The
CLI accepts only an existing `task_id`, derives the host-private V12 shard from
it, and accepts no project root, arbitrary destination, or V11 target.

Backup uses SQLite's online API for the complete project shard. Projection
prune and backup retention default to dry-run and cannot remove canonical rows.
Restore is intentionally offline because the operator CLI has no shared shard
lock with ordinary MCP store processes. Exact `RESTORE`, task, shard, backup,
and `MCP_STOPPED` confirmations document operator intent but do not create
quiescence; all normal MCP access must already be stopped.

## Conditional final documentation stage

Project verification is worker-owned. After its report exists, the coordinator
uses report evidence to decide whether the result changes behavior,
architecture, interfaces, commands, verification guidance, conventions, or
feature ownership. Material impact requires a delegated documentation-sync
update to the harvest documentation under `docs/project/` and `docs/features/`,
followed by a separate delegated verification of grounding, links, commands,
diagrams, and preserved user content.

When no material documentation impact exists, the coordinator records
`documentation not required` with a concise report-grounded rationale and
creates no meaningless edit. The decision stage always occurs before advisory
closure and the final answer; the edit branch remains conditional. Missing
documentation update or verification evidence leads to model-owned rework,
replacement, or explicit risk disclosure rather than backend prohibition.

## Historical compatibility boundary

V11 databases remain untouched in their existing namespace. V12 never migrates,
deletes, reads, or adopts them. V11 tools and unfinished V11 tasks are
incompatible with V12 and cannot be fallback identities or recovery state.

## References

- [orchestration ledger](../features/orchestration-ledger/index.md)
- [advisory governance](../features/advisory-governance/index.md)
- [human-readable task views](../features/human-readable-task-views/index.md)
- [storage classification](storage-classification.md)
- [security policy](../../SECURITY.md)
- [verification](verification.md)

<!-- GENERATED:END -->
