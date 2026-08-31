# Orchestration ledger

<!-- GENERATED:START -->

## Purpose

Cortex V12 is a durable coordination ledger for model-owned multi-agent work.
It stores task intent, bounded delegations, immutable reports, ordered history,
and advisory governance. It does not schedule a workflow, authorize a worker,
or decide when the user may receive an answer. The root coordinator only
orchestrates; workers own all project actions, source/project-grounded access,
and substantive work. Its sole project-read exception is the bounded
orchestrator-owned knowledge route used to compile delegations.

The coordinator maintains an evidence-driven, model-owned orchestration DAG;
it never writes a project solution plan. The current DAG and every revision are
persisted through task-linked initiative revisions and the existing
delegation/report/decision graph, while the backend never executes it. Planning
is optional for genuinely minimal work; light/full delivery requires a `planner`
delegation that publishes the immutable required-review `plan` report and its
exact approval relation. C1/C2/C3 classification remains model-owned
baseline labels—bounded low-risk / multi-step or cross-surface / high-risk or
cross-domain—normally selecting `minimal` / `light` / `full` governance. They
are not server waves, gates, model escalation, or user approval. Report evidence
can add, remove, reorder, retry, or parent-link rework stages without mutating
completed reports.

Every worker performing structural project discovery starts with Codebase
Memory bound to the canonical project root. Exactly one bounded ordinary-search
fallback is allowed only after concrete unavailable, excluded, or insufficient
graph evidence is recorded; silent or chained fallback is invalid.

## Key files

- [cortex.py](../../../plugins/cortex/scripts/cortex.py) builds and serves the V12 facade.
- [public_contracts.py](../../../plugins/cortex/scripts/cortex_runtime/public_contracts.py) owns the exact fourteen-tool semantic catalog.
- [mcp_api.py](../../../plugins/cortex/scripts/cortex_runtime/mcp_api.py) validates requests and serves MCP over stdio.
- [v12_service.py](../../../plugins/cortex/scripts/cortex_runtime/v12_service.py) maps public calls to the store.
- [v12_contract.py](../../../plugins/cortex/scripts/cortex_runtime/v12_contract.py) owns bounded V12 task/report constants and canonical report digests.
- [v12_store.py](../../../plugins/cortex/scripts/cortex_runtime/v12_store.py) owns schema-v1 persistence.
- [v12_projections.py](../../../plugins/cortex/scripts/cortex_runtime/v12_projections.py) materializes derived host-private plan/report Markdown views.
- [worker_message.py](../../../plugins/cortex/scripts/cortex_runtime/worker_message.py) renders the compact native bootstrap and full assignment-read policy/profile package.
- [delegation.py](../../../plugins/cortex/scripts/cortex_runtime/delegation.py) projects coordinator-owned model metadata into the closed native dispatch.
- [model_routing.py](../../../plugins/cortex/scripts/cortex_runtime/model_routing.py) validates exact native model/effort support.

## Data flow

```text
open_task
    │
    ├── assess_governance (append advisory assessment)
    │
    ├── coordinator-held dynamic DAG (not backend workflow state)
    │       ├── optional planner ──► immutable plan report ──► dependent workers
    │       ├── discovery / implementation / review / verification / security
    │       └── evidence may add/remove/reorder/retry/rework later nodes
    │
    ├── open_assignment ──► compact native dispatch ──► native worker
    │                                                   │
    │                         mandatory assignment read ▼
    │                                  full policy/profile/task evidence
    │                                                   │
    │                                                   ▼
    │                                             publish_*
    │                                                   │
    ├──────────────── read_task / bounded worker evidence ◄──┘
    │
    ├── plan report ──► verified review links ──► open_plan_review → record_plan_review
    │
    ├── assess_governance
    │
    ├── worker verification report
    │       └── documentation impact
    │               ├── material ──► docs worker ──► docs verifier
    │               └── none ─────► report-grounded no-docs rationale
    │
    └── close_task ──► localized final remains model-owned

canonical mutation + timeline + projection job
    └── best-effort host-private Markdown view (never project_root)
```

Each semantic mutation appends an ordered private timeline event. Public
`read_task` accepts `view=state|assignment|evidence` and optionally
`continue=true` for the immediately preceding bounded read. It returns
server-produced semantic data with private ledger identity removed; worker
assignment bootstrap is the evidence route.
Only `open_task` carries the exact resolved `project_root` and stores the
canonical project association. Every task-anchored public arrow carries the
14-character `task_ref` (`t_` plus a 12-hex task suffix), which scans only
private V12 shards and fails closed unless it resolves exactly one saved task.
V12 does not infer a root from host
metadata, plugin `cwd`, thread identity, or a hook.

## Exact public operations

| Tool | Semantic contract |
| --- | --- |
| `open_task` | Create a durable task from explicit project root, exact original request/concrete language, English objective, and non-empty meaningful result fields; return preferred `task_ref`. |
| `read_task` | Use `task_ref` with `view=state|assignment|evidence`; use `continue=true` only to resume the immediately preceding bounded read. |
| `open_assignment` | Use `task_ref` to store objective, role, packaged profile, scope, instructions, evidence inputs, and model/effort; return the host-neutral worker bootstrap. |
| `publish_plan` / `publish_result` / `publish_documentation` | The owning worker uses its worker-scoped `task_ref`; Cortex derives the consumed assignment and continuation privately and publishes immutable plan, result, or documentation evidence. |
| `assess_governance` | Use `task_ref` to append a model or user-override assessment. |
| `close_task` | Record the final advisory closure aggregate from durable evidence. |
| `open_clarification` → `record_clarification` | Ask and record one clarification through a matching server-owned binding. |
| `open_plan_review` → `record_plan_review` | Present and record one immutable plan review through a matching server-owned binding. |
| `open_steering` → `record_steering` | Ask and record one steering change through a matching server-owned binding. |

The catalog is identical for coordinators and workers. There is no audience
filter, capability matrix, host-bound lifecycle authority, receipt-gated
selector, tool-name alias, or profile admission rule. Worker handoff reads may
emit immutable delivery receipts, but those receipts are not authority.


Returned task IDs use `task-<64-lowercase-hex-project-shard>-<32-lowercase-hex-record>`
so the runtime can resolve a ledger without scanning project directories. Callers
must still treat the ID as opaque and preserve it exactly.

For public continuation calls, the server-owned continuation returned by
`read_task` is copied unchanged to that operation's continuation input. Durable
IDs, digests, and continuation values are opaque evidence/resumption data, not
callable handles or capability replacements.

Canonical compact decision references match `u_[0-9a-f]{12}`. They remain
opaque evidence values and are copied exactly from successful tool receipts.

Schema failures are sanitized before any mutation. For example, an unsupported
publication field reports the field and expected advertised enum without
echoing the rejected value. Neither diagnostic echoes the rejected value,
and either failure leaves the durable ledger unchanged.

## Finalized-report evidence and advisory closure

`execution_outcome` is neutral finalized-report evidence independent of
advisory governance. It contains `evidence_status`
(`finalized_reports_present` or `no_finalized_reports`),
`finalized_report_count`, `completed_report_count`, and `outcome`. The finalized
count covers every finalized publication. The completed count covers semantically
valid canonical finalized result reports with status `completed`; `outcome` is
`null` until the first semantically valid canonical result is finalized, then
reflects the latest such result as `completed` or `incomplete`. The projection
makes no native-lifecycle claim, and `read_task` always exposes it
independently of closure records.

The coordinator requests `ready`, `ready_with_risks`, or `not_ready` from
sufficient completed evidence and then automatically attempts the advisory
closure write followed by bounded inspection of the intended record. This
policy does not make closure a scheduler, admission check, or user-confirmation
step. `ready_with_risks` needs no user confirmation; any genuine user decision
is handled through ordinary chat and the matching narrow decision record operation only where the
coordinator's plan or product policy requires it.
The closure mutation never upgrades a request. It normalizes an overstated
verdict downward to the current conformance projection and returns the requested
and recorded values without choosing the next orchestration stage.

The ordinary clarification pair (`open_clarification` and
`record_clarification`) records a direct product or requirement answer; it is
not closure review. Once the current result is presented, closure review has
exactly two localized choices: revise the same task or close it. Revision keeps
the same `task_ref`. A later assignment, report, or decision stales a prior
close choice, and the public `close_task` path atomically requires the current
consumed choice. Internal advisory storage can remain policy-neutral because
this requirement is enforced at the public close boundary.

`read_task` also returns `advisory_closure`, whose `record_status` is
`recorded` or `not_recorded` and whose `latest_record` is the latest closure
object or `null`. The `close_task` result adds
`closure_confirmation`: `inspection_status` is `confirmed` or `unconfirmed`,
`reason` is one of `record_inspected`, `persistence_unavailable`,
`inspection_unavailable`, or `record_not_observed`, and `attempts` is 1 or 2.
The service performs at most one same-idempotency retry for a verified transient
persistence or inspection failure. A remaining `unconfirmed` result is honest
advisory bookkeeping uncertainty only: it leaves the independent
`execution_outcome` intact; it does not change the advisory verdict.
The mutation response also includes the current `conformance_review`
projection, complete canonical-body consumption status, and any unresolved
active specialist dispositions, so a recorded advisory verdict cannot be
mistaken for evidence readiness.

## Delegations and report handoff

The coordinator creates a delegation before starting a native worker. The
native worker brief is compact bootstrap context, not the full policy/profile
delivery and not a capability or lifecycle token. The mandatory first
assignment read returns the complete coordinator-authored instructions,
bundled role policy, and task evidence.

The mandatory assignment read contains the canonical saved project root only
for working-directory context. The delegation-specific six-part knowledge contract
is compiled through the bounded route defined only by the orchestrator skill and
is consumed from the full assignment read. Profiles consume that contract after
the first read and do not reconstruct documentation routing.

`scope` is a concise text boundary of worker ownership, not a structured work
plan. It is required, must contain at least one character, and is limited to
65,536 characters. Detailed execution belongs in `instructions`; object-valued
scope is rejected by the closed schema.
Every assignment also selects a non-empty exact set of current outcome-item
references. The selection is required even for planning and single-outcome
work, so prose labels cannot silently widen or replace durable scope.

Workers publish immutable plan, result, or documentation evidence
and return a concise native `Summary` plus exact `Report ref` in the completion
handoff. That summary is routing context, not semantic evidence: before
synthesis, revision, rework, closure, or a final answer, the coordinator reads
each relevant canonical report body through the bounded `read_task` evidence
view to completion and retains its immutable digest receipt. Later delegations
receive only relevant finalized evidence and user-decision refs. A successor
reads predecessor evidence through the bounded `read_task` evidence view when
its declared work requires it. The server rejects evidence outside that
assignment's declared inputs and preserves the bounded evidence contract;
coordinator reads are explicitly classified and cannot be mistaken
for downstream consumption. The server-owned assignment and evidence data
remains authoritative; coordinator prose and native-worker handoffs are routing
context, not semantic evidence. Multiple independently authored reports chosen
by a broad report policy remain valid inputs without implying a unique parent;
private report-reference fields never cross the public error boundary.

Canonical product-facing reports support the fixed
`cortex/report/{progress,result,synthesis,plan}/v1` schemas and additive
`cortex/report/{result,synthesis,plan}/v2` schemas. V2 carries structured
contract coverage, deviations, unresolved items, risks, and verification. V1,
legacy, and semantic-invalid stored JSON remain immutable readable evidence. A
semantic-valid canonical payload may carry one optional `source_text` value
unchanged; it has no language tag or translated/original duplicate. Only a
finalized, completed, semantic-valid canonical plan may produce a ready
approval relation. This classification is evidence, not a scheduler or
lifecycle gate.

New specialist plan and result reports use the current `v3` evidence envelope.
Every publication supplies one disposition for each item in its immutable
assignment scope. Worker bootstrap returns a server-owned ordered
pre-publication reconciliation receipt: its template contains every required
item reference without inventing a disposition, and its count and ordered
reference sequence are the final pre-call completeness check. Compatible
repeated rows for the same item and status are
coalesced into one canonical disposition while preserving every unique
verification fact; conflicting repeated statuses, missing items, extra items,
or unsupported claims are rejected before the terminal publication slot is
consumed. Before finalization it requires exact coverage, observable executed or
explicitly-not-run checks, residual risks/deviations/unresolved items, and a
documentation-impact decision for result/synthesis reports. A planner maps the complete current effective
contract—one item per independent user outcome, with acceptance, verification,
constraints, steer extensions, and provenance linked to that outcome—using the
stable item references returned in its semantic brief, independent of delivery
assignments. It maps every current item exactly
once and provides ordered stages with an owner, earlier dependencies, work, and
verification. An incomplete or predictably semantic-invalid V3 report remains
assembling for corrective append; a corrective coverage mapping is the final
canonical view while prior immutable chunks remain history. Finalization does
not consume a normal result slot. V1/V2 history remains readable without reset
or migration.
Planner-authored implementation microtasks, when present in plan evidence,
are likewise evidence for the model-owned orchestration DAG: they carry
ownership, dependencies, acceptance, and verification context but are not
backend jobs, scheduler entries, workflow gates, or a worker-subtask copy in
the standard Codex To-Do projection. The coordinator may use that evidence to
add, remove, reorder, or rework worker stages while completed reports remain
immutable.
Independent delegations may proceed concurrently, and a missing worker report
does not block replacement or synthesis. Status is `partial`, `completed`,
`blocked`, or `failed`; it does not imply acceptance or native termination.

## Effective outcome coverage

The immutable task/result contract remains the original record of intent.
`read_task` projects its active, revisioned effective contract as one stable
`o_` item reference per independent user outcome. Acceptance and verification
criteria, task constraints, steer additions, and exact source fragments are
linked to that outcome rather than materialized as more items. Delegations
explicitly associate their current
outcomes as `owned`, `contributing`, or `evidence-producing`; each active item has
at most one owner, while support and evidence roles may be shared.

Finalized V2/V3 report coverage is accepted only for item references assigned to
that report's immutable delegation snapshot. Aggregate coverage considers
only active items and labels each one `complete`, `missing`, `partial`,
`unverified`, `stale`, or `contradictory`. A user `steer` decision creates a
new revision, replaces only each named outcome with its merged linked data, and
unaffected references and evidence remain valid. The advisory conformance
projection relates the active revision, private decision/report evidence,
complete server-owned reads, unresolved
specialist dispositions, and aggregate coverage. It
guides model-owned rework, verification, and closure review; it cannot alter
report/delegation lifecycle or block safe coordination.

Private/internal report storage may retain immutable assembly metadata and
chunking limits, but the public facade has no `begin`/`append`/`finalize`/`abort`
operations. Public workers submit one complete plan, result, or documentation
payload through the corresponding publication operation.

Worker evidence reads are exposed through the bounded `read_task` evidence
view. The public caller supplies no publication/assignment reference or cursor;
continuation is retained by the server. Private/internal
assembly state is never a public capability or recovery input.

The read service preflights the bounded task/evidence response before
materializing it.
Projection rendering likewise preflights its aggregate output (512 files,
32 MiB total, and 10 MiB per file) so an over-limit request produces no
partial artifact set.

`read_task` returns only the selected semantic view and its server-owned
continuation; it does not infer unlinked private/internal initiative history.
Private/internal storage repair may append only missing derived events and
must never rewrite existing timeline rows or guess ambiguous lineage.

Plan reports add `review_policy=informational|required` and may name a
finalized predecessor. A required review is a coordinator-owned interaction;
for light/full delivery the backend admits only an explicit approval bound to
the exact current plan. The matching narrow decision record operation preserves an
exact original response and attribution `user_via_coordinator` through one
closed operation-specific request containing the task reference, neutral prompt
or response, and user language as advertised; retired English duplicate fields
are rejected. Decision subject and digest binding are derived privately from
the matching open operation, never supplied as report/delegation locators.
Approval does not transfer to a revised private plan identity, and clarification
is not approval.

## Coordinator-only execution boundary

The coordinator may define outcome/acceptance, select or revise governance,
open and read tasks, open assignments, choose exact worker model/effort,
coordinate native workers, read server-produced evidence, decide
rework/replacement, record
advisory closure, and synthesize the final answer. Before project delegation,
the host-injected `AGENTS.md` context governs the task; it then reads the project and feature indexes, and
task-relevant pages
selected from those indexes. It may not inspect or search source/code/config,
scan unrelated documentation, create or edit target-project files, perform
substantive domain analysis, or run project commands, builds, tests, browsers,
or direct verification.

The route permits only non-shell direct reads of already-known exact allowed
paths. It does not permit shell or command use, `rg`, `find`, globs,
graph/source/repository search, directory listing, or candidate-path probes.
Unknown roots or paths and unavailable direct readers require a native
discovery/retrieval worker.

All project discovery, project-state analysis, implementation, documentation
work, review, and verification belong to workers. The coordinator responds to
missing or contradictory evidence by creating another delegation, never by
crossing into the project.

Project-state analysis includes root discovery, Git, manifests, caches,
worktrees, existence/absence or unchanged-state, and project-local `.codex`.
These checks stay delegated when read-only, performed before plan review, used
to recover from a report gap, or explicitly requested from the coordinator.

The coordinator communicates with the user in the user's latest meaningful
language. Worker-authored messages, report narrative, plans, normalized
decisions, governance records, timeline records, and generated plan/report
Markdown are English. Canonical product-facing reports and handoff payloads
may carry one optional `source_text` value unchanged as labeled inert source
material; they do not require language tags or translated/original duplicates.
Task contracts retain exact original/language fields. Decision contracts use
neutral `prompt`, exact `response_original`, and `user_language`; retired
`prompt_en` and `response_en` fields are rejected. Source text is never treated as an unquoted
worker instruction. A task-required product/output language remains part of
the delegated product contract.

The native dispatch projection does not select an isolated worktree or
workspace. Physical concurrent-writer isolation therefore remains an
unconfirmed host capability outside this ledger; the package neither
implements nor claims collision prevention until a supported host mechanism
and lifecycle owner are supplied.

Coordinator-facing communication follows the packaged result → impact → next
step policy. It suppresses unchanged waiting updates, defaults to hiding opaque
IDs and ledger/governance jargon, and reveals technical detail progressively;
safe contextual humor is optional only after the material fact. This is a
prompt-policy integration, not a runtime dispatcher or model evaluation gate.

After the project verifier reports, the coordinator evaluates documentation
impact from report evidence. Material changes require a documentation-sync
worker and a separate documentation-verifier worker; no impact requires a
concise report-grounded `documentation not required` rationale without an edit.
This is a model-owned stage before closure. Missing documentation evidence can
cause rework, replacement, or disclosed risk but never backend prohibition.

## Model and native transport

The coordinator owns advisory profile, exact model, and reasoning effort. The
ledger may store logical model/effort metadata for audit but never selects or
rewrites it.

Luna, Terra, and Sol each support `low`, `medium`, `high`, `xhigh`, and `max`.
Native arguments use `fork_turns="none"`, keep the effort unchanged, omit
`model` for logical Luna, and pass exact Terra/Sol overrides. There is no
server-owned fallback ladder or worker reconstruction.

## Storage and idempotency

Each resolved project has a separate schema-v1 database at
`~/.codex/cortex/v12/projects/p-<project-hash>/cortex.db`. Bootstrap validates
the V12 application ID, migration row, schema version, and project hash. The
first normal path-bearing task creation automatically upgrades the one exact
released pre-human-view V12 layout transactionally; unknown or future layouts
remain fail-closed, and V11 is never a migration input.

Mutations run transactionally and require caller-generated idempotency keys.
The same normalized payload/key returns the original
result; conflicting payload returns `idempotency_conflict` without mutation. Concurrent reports,
assessments, and initiative revisions receive unique ordered sequences.

The database is the sole authority. It also owns report manifests/chunks,
plan metadata, append-only user decisions, and projection jobs. Every canonical
mutation commits its timeline event and projection job atomically; Markdown
materialization occurs afterward and can fail without rolling back or blocking
the ledger.

Derived views live only below the host-private
`tasks/<task_ref>/` subtree beside the database: task/index pages, current and
immutable plan pages, delegation/report/decision pages, and a paged timeline.
Directories are `0700`; database/WAL/SHM and Markdown files are `0600`.
Generated content is never parsed back. Cortex never creates a `.codex`
directory, view, ledger, report, plan, or ignore rule below `project_root`.

The dynamic `human_view` status is `ready`, `stale`, `conflict`, `unavailable`,
or `disabled`. Only a current, digest-verified, regular non-symlink file inside
the exact private task subtree receives an absolute path. Direct edits are
preserved as conflicts. The coordinator links a verified ready view with a
localized summary during plan review, progress, report decisions, user
decisions, and final synthesis. Otherwise it discloses the status and
summarizes canonical evidence inline; view failure is never a lifecycle gate.

## Nonblocking boundary

Core tools validate public shape/size, strict JSON, identifier/reference
existence, project isolation, transaction/foreign-key integrity, and
idempotency. Assignment admission also requires one governance assessment;
light/full delivery additionally requires the exact current finalized
required-review plan approval. Initiative status, dependency warnings, closure
verdict, and closure presence never admit an operation.

No host epoch, stop marker, mandatory wait, repair escrow, resource lock,
closure breaker, or server recovery route exists. The narrow assessment and
plan-approval checks do not schedule work, prevent planning/evidence assignments,
or block an already bound worker. Native execution remains model/host behavior
outside the ledger.

## MCP result transport

Every one of the fourteen registry entries advertises its closed input schema.
The runtime separately retains the family-specific successful-result schema
and uses those definitions to validate inputs and successful results. A
successful call returns canonical JSON as text content and `structuredContent`,
with `isError=false`. A
caller-correctable error returns `isError=true` with one bounded sanitized text
message plus a matching sanitized `structuredContent.error` containing the
stable code, safe details, and recovery action. If multiple required input
properties are absent, safe details include the complete bounded
`missing_fields` list. This error object is not a successful-output-schema
variant. Sanitized JSON-RPC internal errors cover server-state faults. None of
these transport paths may expose private
task/report content, secrets, raw diagnostics, filesystem state, or ledger rows.

## Historical compatibility boundary

V12 starts a new database family and never opens or migrates V11 state. V11
databases remain untouched. V11 tools and unfinished V11 tasks are incompatible
with the V12 catalog and cannot provide fallback identity or recovery.

## Verification

See [verification.md](../../project/verification.md),
[storage-classification.md](../../project/storage-classification.md),
[advisory governance](../advisory-governance/index.md),
[human-readable task views](../human-readable-task-views/index.md), and
[gotchas.md](../../project/gotchas.md).

<!-- GENERATED:END -->
