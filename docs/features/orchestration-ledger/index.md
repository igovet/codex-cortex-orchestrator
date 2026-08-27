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
- [public_contracts.py](../../../plugins/cortex/scripts/cortex_runtime/public_contracts.py) owns the exact eleven-tool catalog.
- [mcp_api.py](../../../plugins/cortex/scripts/cortex_runtime/mcp_api.py) validates requests and serves MCP over stdio.
- [v12_service.py](../../../plugins/cortex/scripts/cortex_runtime/v12_service.py) maps public calls to the store.
- [v12_contract.py](../../../plugins/cortex/scripts/cortex_runtime/v12_contract.py) owns bounded V12 task/report constants and canonical report digests.
- [v12_store.py](../../../plugins/cortex/scripts/cortex_runtime/v12_store.py) owns schema-v1 persistence.
- [v12_projections.py](../../../plugins/cortex/scripts/cortex_runtime/v12_projections.py) materializes derived host-private plan/report Markdown views.
- [worker_message.py](../../../plugins/cortex/scripts/cortex_runtime/worker_message.py) renders the direct native worker message from bundled policy and coordinator-authored delegation data.
- [delegation.py](../../../plugins/cortex/scripts/cortex_runtime/delegation.py) projects coordinator-owned model metadata to native spawn arguments.
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
    ├── create_delegation ──► direct worker brief ──► native worker
    │                                                │
    │                                                ▼
    │                                          submit_report
    │                                                │
    ├────────────── inspect_task / bounded read_reports ◄────┘
    │
    ├── plan report ──► verified review links ──► record_user_decision
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
| `inspect_task` | Use `task_ref` to return compact task history after `after_sequence`, bounded by `limit`. |
| `create_delegation` | Use `task_ref` to store objective, separate human `role`, exact packaged `profile_name`, required textual `scope`, instructions, report/decision inputs, and exact model/effort; return a direct worker brief carrying the saved root. |
| `read_delegation` | Use `delegation_id` plus `after_sequence` to resolve its owner task and return compact local history without a receipt. Do not supply `task_ref` or `task_id`; any legacy direct-service compatibility stays below the public MCP schema. |
| `submit_report` | Use `delegation_id` for single or stable-ID `begin`/`append`/`finalize`/`abort` progress, result, synthesis, or plan evidence. It resolves the owner task; do not supply `task_ref` or `task_id`. |
| `read_reports` | Use report IDs to resolve their owner task and read metadata or whole JSON chunks for 1–20 known reports in requested order, with section/cursor/byte bounds. Do not supply `task_ref` or `task_id`. |
| `set_governance_mode` | Use `task_ref` to append a model or user-override assessment. |
| `record_initiative` | Use `task_ref` only as the project anchor to create or revise an initiative and its links. |
| `inspect_governance` | Use `task_ref` to read bounded project/task/initiative assessments, revisions, links, warnings, and closures. |
| `submit_governance_closure` | Use `task_ref` to append an advisory verdict and evidence for required `subject_type` plus existing task/initiative `subject_id`. |
| `record_user_decision` | Use `task_ref` to append coordinator-attributed original/English user evidence for an exact task/plan/initiative/delegation/report; bind plan/report to the canonical digest. |

The catalog is identical for coordinators and workers. There is no audience
filter, capability matrix, host-bound authority, read receipt, selector,
tool-name alias, or profile admission rule.

Returned task IDs use `task-<64-lowercase-hex-project-shard>-<32-lowercase-hex-record>`
so the runtime can resolve a ledger without scanning project directories. Callers
must still treat the ID as opaque and preserve it exactly.

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
body merely to summarize it. Later delegations receive only relevant finalized report IDs, their exact
manifest digests, and user-decision IDs. A successor must call `read_reports`
with `reader_kind="worker"` and its own `consumer_delegation_id` before using a
predecessor report. The server rejects a report outside that delegation's
declared inputs and appends a structural receipt for every returned page:
consumer delegation, report ID, observed manifest digest, selected sections,
input/output cursors, returned chunk indexes, byte count, and chronology
sequence. Coordinator reads are explicitly classified and cannot be mistaken
for downstream consumption. Receipts prove ledger delivery, not semantic use of
opaque report prose or native-worker lifecycle.
Independent delegations may proceed concurrently, and a missing worker report
does not block replacement or synthesis. Status is `partial`, `completed`,
`blocked`, or `failed`; it does not imply acceptance or native termination.

`submit_report` defaults to `single`, which accepts one complete JSON body up to
64 KiB. A larger report uses one stable ID across `begin`, sequential labeled
`append` chunks up to 32 KiB, exact-manifest `finalize`, or `abort`. The limits
are 256 chunks and 8 MiB per report, eight assembling reports and 16 MiB of
assembling content per task, and 128 MiB retained report content per task.
Report timeline events record start, each accepted `report_chunk_appended`,
final submission, or abort. A duplicate identical chunk is safe; a conflicting
or out-of-order chunk is rejected.

`read_reports` supports up to 32 section labels, an opaque scoped cursor, and a
65,536-byte maximum/default `max_bytes` budget (`0` means metadata only).
Deprecated `byte_budget` is an equivalent compatibility alias; different
simultaneous values are rejected. It returns only
whole JSON chunks under a 224 KiB response ceiling. A small finalized one-chunk
report may additionally expose compatibility `content`; incomplete/aborted
content is never presented as completed evidence. Recovery resumes from
canonical report metadata and cursor state, never from a generated Markdown
view. The host-private task projection also contains the bounded receipt list,
so database, timeline, and projection evidence agree without adding a twelfth
public tool.

`inspect_task`, `read_delegation`, and `inspect_governance` return only the
task-scoped timeline page selected by `after_sequence` and `limit`; they do not
infer unlinked initiative history. On a normal V12 store open, the one-time
conservative backfill appends only missing derived task events (including report
chunk appends and unambiguous initiative/closure lineage), marks their payloads
as `backfill` data, and queues derived-view refreshes. It never rewrites an
existing timeline row or guesses ambiguous report-only lineage.

Plan reports add `review_policy=informational|required` and may name a
finalized predecessor. A required review is a coordinator-owned pause for
plan-dependent work, not a backend gate. `record_user_decision` preserves an
exact response plus its English normalization and attribution
`user_via_coordinator`. Plan/report decisions require the canonical digest;
only plan `approve` also requires the current ready approval-view
digest/source sequence and opaque handle. Plan `request_revision` and `cancel`
preserve the exact plan digest/response without volatile view binding, so
unrelated timeline events do not block feedback. Approval does not transfer to
a revised report ID/digest, and clarification is not approval.

## Coordinator-only execution boundary

The coordinator may define outcome/acceptance, select or revise governance,
create and inspect tasks/delegations, choose exact worker model/effort,
coordinate native workers, read reports, decide rework/replacement, record
advisory closure, and synthesize the final answer. Before project delegation,
it must read applicable `AGENTS.md`, the project and feature indexes, and
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
language. Worker messages, worker reports, plans, normalized decisions,
governance records, timeline records, and generated plan/report Markdown are
English. Exact user text is retained only in labeled original fields beside
separate English-normalized fields and is never treated as unquoted worker
instruction. A task-required product/output language remains part of the
delegated product contract.

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

Mutations run transactionally. Optional idempotency keys are scoped by
operation. Same normalized payload returns the original result; conflicting
payload returns `idempotency_conflict` without mutation. Concurrent reports,
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

Every one of the eleven registry entries advertises both its closed input schema
and successful `outputSchema`; the runtime uses those same definitions to
validate inputs and successful results. A successful call returns canonical JSON
as text content and `structuredContent`, with `isError=false`. A
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
