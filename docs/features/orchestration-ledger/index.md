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
is an optional `planner` delegation that publishes the immutable `plan` report
named as predecessor by plan-dependent workers. C1/C2/C3 remain advisory
baseline labels—bounded low-risk / multi-step or cross-surface / high-risk or
cross-domain—normally selecting `minimal` / `light` / `full` governance. They
are not server waves, gates, model escalation, or user approval. Report evidence
can add, remove, reorder, retry, or parent-link rework stages without mutating
completed reports.

## Key files

- [cortex.py](../../../plugins/cortex/scripts/cortex.py) builds and serves the V12 facade.
- [public_contracts.py](../../../plugins/cortex/scripts/cortex_runtime/public_contracts.py) owns the exact fifteen-tool semantic catalog.
- [mcp_api.py](../../../plugins/cortex/scripts/cortex_runtime/mcp_api.py) validates requests and serves MCP over stdio.
- [v12_service.py](../../../plugins/cortex/scripts/cortex_runtime/v12_service.py) maps public calls to the store.
- [v12_contract.py](../../../plugins/cortex/scripts/cortex_runtime/v12_contract.py) owns bounded V12 task/report constants and canonical report digests.
- [v12_store.py](../../../plugins/cortex/scripts/cortex_runtime/v12_store.py) owns schema-v1 persistence.
- [v12_projections.py](../../../plugins/cortex/scripts/cortex_runtime/v12_projections.py) materializes derived host-private plan/report Markdown views.
- [worker_message.py](../../../plugins/cortex/scripts/cortex_runtime/worker_message.py) renders the direct worker message from bundled policy and coordinator-authored delegation data.
- [delegation.py](../../../plugins/cortex/scripts/cortex_runtime/delegation.py) projects coordinator-owned model metadata into the host-neutral `dispatch_brief`.
- [model_routing.py](../../../plugins/cortex/scripts/cortex_runtime/model_routing.py) validates exact native model/effort support.

## Data flow

```text
create_task
    │
    ├── set_governance_mode (append advisory assessment)
    │
    ├── coordinator-held dynamic DAG (not backend workflow state)
    │       ├── optional planner ──► immutable plan report ──► dependent workers
    │       ├── discovery / implementation / review / verification / security
    │       └── evidence may add/remove/reorder/retry/rework later nodes
    │
    ├── create_delegation ──► dispatch_brief + renderer proof ──► native worker
    │                                                │
    │                                                ▼
    │                                          submit_report
    │                                                │
    ├────────────── inspect_task / bounded read_reports ◄────┘
    │
    ├── plan report ──► verified review links ──► open_plan_review → record_plan_review
    │
    ├── record_initiative / inspect_governance
    │
    ├── worker verification report
    │       └── documentation impact
    │               ├── material ──► docs worker ──► docs verifier
    │               └── none ─────► report-grounded no-docs rationale
    │
    └── submit_governance_closure ──► localized final remains model-owned

canonical mutation + timeline + projection job
    └── best-effort host-private Markdown view (never project_root)
```

Each semantic mutation also appends an ordered timeline event. Inspection uses
`after_sequence=0` and `limit=50` by default (limit 1–200), and returns
`next_sequence` plus `has_more` for bounded incremental context recovery.
Task/delegation inspection returns
compact report references; `read_reports` is the only report body/chunk reader,
and large reports stay section- and byte-bounded.
Only `create_task` carries the exact resolved `project_root` and stores the
canonical project association. Every task-anchored public arrow carries the
14-character `task_ref` (`t_` plus a 12-hex task suffix), which scans only
private V12 shards and fails closed unless it resolves exactly one saved task.
V12 does not infer a root from host
metadata, plugin `cwd`, thread identity, or a hook.

## Exact public operations

| Tool | Semantic contract |
| --- | --- |
| `create_task` | Create a durable task from explicit project root, exact original request/concrete language, English objective, and non-empty meaningful `cortex/task-contract/v1` result fields; return preferred `task_ref` and canonical `task_id`. |
| `inspect_task` | Use `task_ref` to return compact task history after `after_sequence`, bounded by `limit`, plus exact persisted continuation dispatches for host reconciliation. The result includes independent `execution_outcome` and task-relevant `advisory_closure` projections. Continuations are lifecycle-unknown and require a finalized report, explicit blocked/partial handoff, or parent-linked replacement before a durable successor relies on them. |
| `create_delegation` | Use `task_ref` to store objective, separate human `role`, exact packaged `profile_name`, required textual `scope`, instructions, report/decision inputs, and exact model/effort; return a host-neutral `dispatch_brief` and renderer/profile proof. Codex maps the brief to its active spawn operation; `read_delegation` is recovery-only, and the receipt is not host authority. |
| `read_delegation` | Use the exact emitted `delegation_ref` plus `after_sequence` to resolve its owner task and return compact local history without a receipt. Do not supply `task_ref` or `task_id`; no legacy or direct-ID public shape is accepted. |
| `submit_report` | Use the exact emitted `delegation_ref` for an assembled `begin`/sequential `append`/`finalize`/`abort` progress, result, synthesis, or plan report. It resolves the owner task; do not supply `task_ref` or `task_id`. |
| `read_reports` | Use exact `report_refs` to resolve their owner task and read metadata for coordinators or whole JSON chunks for a consuming worker (with its exact `consumer_delegation_ref` and declared inputs), for 1–20 known reports in requested order, with section/cursor/integer-byte bounds. Do not supply `task_ref` or `task_id`. |
| `set_governance_mode` | Use `task_ref` to append a model or user-override assessment. |
| `record_initiative` | Use `task_ref` only as the project anchor to create or revise an initiative and its links. |
| `inspect_governance` | Use `task_ref` to read bounded project/task/initiative assessments, revisions, links, warnings, and closures. |
| `submit_governance_closure` | After sufficient finalized worker evidence, use `task_ref` to append an advisory verdict and evidence for required `subject_type` plus matching compact task/initiative `subject_ref`; the service automatically inspects the intended record and returns `closure_confirmation` separately from `execution_outcome`. |
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

For public continuation calls, `handles.after_sequence` and
`handles.idempotency_key` are copied unchanged to
their identically named literal inputs. `handles.cursor` is the separate opaque
`read_reports` continuation value. Root-level `next_sequence` and
`next_chunk_index` describe the returned receipt; they are informational, not
`handles` aliases, and `retry_handle` is not a callable handle replacement.

Canonical compact decision references match `u_[0-9a-f]{12}`. They remain
opaque evidence values and are copied exactly from successful tool receipts.

Schema failures are sanitized before any mutation. For example, an unsupported
`submit_report.report_type` reports `Field: report_type. Expected:
progress|result|synthesis|plan. Reason: enum.` An invalid fifth
`read_reports.report_refs` value reports `Field: report_refs. Expected:
r_[0-9a-f]{12}. Reason: pattern.` Neither diagnostic echoes the rejected value,
and either failure leaves the durable ledger unchanged.

## Finalized-report evidence and advisory closure

`execution_outcome` is neutral finalized-report evidence independent of
advisory governance. It contains `evidence_status`
(`finalized_reports_present` or `no_finalized_reports`),
`finalized_report_count`, `completed_report_count`, and `outcome`. The finalized
count covers every finalized report. The completed count covers semantically
valid canonical finalized result reports with status `completed`; `outcome` is
`null` until the first semantically valid canonical result is finalized, then
reflects the latest such result as `completed` or `incomplete`. The projection
makes no native-lifecycle claim, and `inspect_task` always exposes it
independently of closure records.

The coordinator selects `ready`, `ready_with_risks`, or `not_ready` from
sufficient completed evidence and then automatically attempts the advisory
closure write followed by bounded inspection of the intended record. This
policy does not make closure a scheduler, admission check, or user-confirmation
step. `ready_with_risks` needs no user confirmation; any genuine user decision
is handled through ordinary chat and the matching narrow decision record operation only where the
coordinator's plan or product policy requires it.

`inspect_task` also returns `advisory_closure`, whose `record_status` is
`recorded` or `not_recorded` and whose `latest_record` is the latest closure
object or `null`. The `submit_governance_closure` result adds
`closure_confirmation`: `inspection_status` is `confirmed` or `unconfirmed`,
`reason` is one of `record_inspected`, `persistence_unavailable`,
`inspection_unavailable`, or `record_not_observed`, and `attempts` is 1 or 2.
The service performs at most one same-idempotency retry for a verified transient
persistence or inspection failure. A remaining `unconfirmed` result is honest
advisory bookkeeping uncertainty only: it leaves the independent
`execution_outcome` intact; it does not change the advisory verdict.

## Delegations and report handoff

The coordinator creates a delegation before starting a native worker. The
delegation's worker brief combines untrusted task context with exact
coordinator-authored instructions and bundled role policy; it is not a
capability or lifecycle token.

The native brief contains the canonical saved project root for working-directory
context. The coordinator also embeds the delegation-specific six-part knowledge
contract compiled through the bounded route defined only by the orchestrator
skill. Profiles consume that contract and do not reconstruct documentation
routing.

`scope` is a concise text boundary of worker ownership, not a structured work
plan. It is required, must contain at least one character, and is limited to
65,536 characters. Detailed execution belongs in `instructions`; object-valued
scope is rejected by the closed schema.

Workers publish immutable `progress`, `result`, `synthesis`, or `plan` reports
and return a concise native `Summary` plus exact `Report ref` in the completion
handoff. The coordinator consumes that handoff without rereading the report
body merely to summarize it. Later delegations receive only relevant finalized report refs, their exact
manifest digests, and user-decision refs. A successor must call `read_reports`
with its own exact `consumer_delegation_ref` before
using a predecessor report. The server rejects a report outside that delegation's
declared inputs and appends a structural receipt for every returned page:
consumer delegation ref, report ref, observed manifest digest, selected sections,
input/output cursors, returned chunk indexes, byte count, and chronology
sequence. Coordinator reads are explicitly classified and cannot be mistaken
for downstream consumption. Receipts prove ledger delivery, not semantic use of
opaque report prose or native-worker lifecycle.

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
Before finalization it requires exact coverage, observable executed or
explicitly-not-run checks, residual risks/deviations/unresolved items, and a
documentation-impact decision. A planner maps the complete current effective
contract—requirements, constraints, acceptance criteria, and derived
verification items—using the stable item references returned in its semantic
brief, independent of delivery assignments. It maps every current item exactly
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
`inspect_task` projects its active, revisioned effective contract as stable
`o_` item references across requirements, constraints, acceptance criteria,
and verification expectations. Delegations explicitly associate their current
items as `owned`, `contributing`, or `evidence-producing`; each active item has
at most one owner, while support and evidence roles may be shared.

Finalized V2 report coverage is accepted only for item references assigned to
that report's delegation at the active revision. Aggregate coverage considers
only active items and labels each one `complete`, `missing`, `partial`,
`unverified`, `stale`, or `contradictory`. A user `steer` decision creates a
new revision, retires only its named items, and adds only its stated items;
unaffected references and evidence remain valid. The advisory conformance
projection relates the active revision, decision refs, finalized report
manifests, completed coordinator-read digests, and aggregate coverage. It
guides model-owned rework, verification, and closure review; it cannot alter
report/delegation lifecycle or block safe coordination.

Every new report uses one stable ID across `begin`, sequential labeled `append` chunks up to
32 KiB, exact-manifest `finalize`, or `abort`. The limits
are 256 chunks and 8 MiB per report, eight assembling reports and 16 MiB of
assembling content per task, and 128 MiB retained report content per task.
Report timeline events record start, each accepted `report_chunk_appended`,
final submission, or abort. A duplicate identical chunk is safe; a conflicting
or out-of-order chunk is rejected.

`read_reports` supports up to 32 section labels, an opaque scoped cursor, and a
fixed 65,536-byte server page; metadata-only reads omit a consuming delegation.
It returns only
whole JSON chunks under a 224 KiB response ceiling. A small finalized one-chunk
report may additionally expose compatibility `content`; incomplete/aborted
content is never presented as completed evidence. Recovery resumes from
canonical report metadata and cursor state, never from a generated Markdown
view. The host-private task projection also contains the bounded receipt list,
so database, timeline, and projection evidence agree without adding a twelfth
public tool.

The read service preflights the complete report/chunk request and encoded
response before materializing bodies, including the 224 KiB response ceiling.
Projection rendering likewise preflights its aggregate output (512 files,
32 MiB total, and 10 MiB per file) so an over-limit request produces no
partial artifact set.

`inspect_task`, `read_delegation`, and `inspect_governance` return only the
task-scoped timeline page selected by `after_sequence` and `limit`; they do not
infer unlinked initiative history. On a normal V12 store open, the one-time
conservative backfill appends only missing derived task events (including report
chunk appends and unambiguous initiative/closure lineage), marks their payloads
as `backfill` data, and queues derived-view refreshes. It never rewrites an
existing timeline row or guesses ambiguous report-only lineage.

Plan reports add `review_policy=informational|required` and may name a
finalized predecessor. A required review is a coordinator-owned pause for
plan-dependent work, not a backend gate. The matching narrow decision record operation preserves an
exact original response and attribution `user_via_coordinator` through one
closed canonical request containing task and subject refs, decision type,
neutral `prompt`, exact `response_original`, and user language; retired English
duplicate fields are rejected. Plan/report decisions additionally require the
canonical
digest; only plan `approve` also requires the current ready approval-view
digest/source sequence and opaque handle copied from one returned relation. Plan
`request_revision` and `cancel`
preserve the exact plan digest/response without volatile view binding, so
unrelated timeline events do not block feedback. Approval does not transfer to
a revised report ID/digest, and clarification is not approval.

## Coordinator-only execution boundary

The coordinator may define outcome/acceptance, select or revise governance,
create and inspect tasks/delegations, choose exact worker model/effort,
coordinate native workers, read reports, decide rework/replacement, record
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
idempotency. They never inspect mode, initiative status, dependency warnings,
report completion, closure verdict, or closure presence to admit an operation.

No V12 lifecycle hook, host epoch, child binding, stop marker, mandatory wait,
backend plan-approval gate, receipt, repair escrow, resource lock, closure
breaker, or server recovery route exists. A coordinator may pause
plan-dependent work for a genuine required user review; public ledger tools
remain available throughout. Native execution remains model/host behavior
outside the ledger.

## MCP result transport

Every one of the fifteen registry entries advertises its closed input schema.
The runtime separately retains the family-specific successful-result schema
and uses those definitions to validate inputs and successful results. A
successful call returns canonical JSON as text content and `structuredContent`,
with `isError=false`. A
caller-correctable error returns `isError=true` with one bounded sanitized text
message, stable code, and recovery action, but no `structuredContent`—an error
is not a successful-output-schema variant. Sanitized JSON-RPC internal errors
cover server-state faults. None of these transport paths may expose private
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
