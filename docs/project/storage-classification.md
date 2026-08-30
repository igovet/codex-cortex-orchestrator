# Storage classification

<!-- GENERATED:START -->

Status: Cortex V12 implementation contract, SQLite schema v1.

## Decision

The V12 project database is the only authoritative mutable Cortex store:

```text
~/.codex/cortex/v12/projects/p-<sha256-of-resolved-project-root>/cortex.db
```

The database is a durable coordination ledger. It does not store native host
authority and cannot decide whether the model may delegate, read evidence,
rework, close, or answer the user.

Only `create_task` carries the exact resolved `project_root` and stores the
canonical association on the task. The seven task-anchored public calls carry
the compact `task_ref`; the three entity-derived public calls resolve their
owner from delegation or report refs. A `task_ref` is `t_` plus a 12-hex task
suffix, scanned only across private V12 shards and rejected on zero or ambiguous
matches. Full `task_id` remains canonical database evidence, not a public
request locator. No trusted MCP metadata, plugin process `cwd`,
thread identity, or lifecycle hook supplies the root. The native worker brief
carries the saved root for working-directory context.
Optional task `context` is arbitrary JSON rather than a root binding. The task
also stores its complete versioned result contract: exact original user request,
user language, English objective, requirements, constraints, acceptance
criteria, and verification plan.

## Classification matrix

| Data | Location | Writers/readers | Classification and retention |
| --- | --- | --- | --- |
| Schema and project metadata | `schema_migrations`, `v12_metadata`, SQLite pragmas | Store bootstrap/validation | Canonical database-family, schema-v1, and project-hash integrity metadata |
| Task/result contract | `tasks` | `create_task`; `inspect_task` | Canonical project-scoped exact original request/language plus English outcome, requirements, constraints, acceptance criteria, verification plan, and bounded context |
| Effective outcome contract | `effective_contract_revisions`, `effective_contract_items` | Bootstrap, user `steer` decision; `inspect_task` | Revisioned active view of task-contract items with stable `o_` references; retired items remain historical while unaffected active references stay stable |
| Delegation assignments and projected briefs | `delegations` plus its saved task association | Coordinator `create_delegation`; delegation/task reads | Canonical bounded assignment with required textual ownership scope, exact model/effort, and compiled knowledge contract in `instructions`; the projected native brief adds the task's saved root for context, never host authority |
| Outcome assignments and coverage | `delegation_outcome_assignments`, `report_contract_coverage` | Delegation/report writes; `inspect_task` aggregate/conformance projections | Per-revision owned/contributing/evidence-producing responsibility and immutable finalized-report coverage claims with verification details; used to identify missing, partial, unverified, stale, and contradictory active evidence, never a backend gate |
| Worker evidence | `reports`, `report_chunks`, `report_usage` | `submit_report`; compact task/delegation references; bounded `read_reports` body/chunk reads | Immutable progress/result/synthesis/plan content, manifest/digest, review policy, assembly state, chunks, and quotas; private and potentially sensitive |
| Execution-outcome projection | Derived from finalized rows in `reports` | `inspect_task`; `submit_governance_closure` result | Exact `execution_outcome` fields are `evidence_status`, `finalized_report_count`, `completed_report_count`, and `outcome`: every finalized report contributes to the first count, while only semantically valid canonical finalized results determine the completed count and nullable `completed`/`incomplete` outcome, independently of advisory closure bookkeeping and without claiming native lifecycle |
| User decision evidence | `user_decisions` | narrow decision record operations; task/governance references and decision views | Append-only coordinator-asserted ordinary-chat response, neutral prompt, exact original-language response, language, subject binding/digest, supersession, and `user_via_coordinator` attribution; evidence only, never authentication or authority |
| Mode history | `governance_assessments` | `set_governance_mode`; governance/task reads | Append-only advisory model/user-override assessments |
| Initiative projection | `initiatives` | `record_initiative` and initiative closure | Current project-level goal/risk/status/notes projection |
| Initiative history | `initiative_revisions` | Initiative writes/closure; governance reads | Append-only revisions including link state |
| Initiative relationships | `initiative_links` | `record_initiative`; governance reads | Current parent/dependency/task/report links and unresolved/cyclic warnings |
| Closure statements | `governance_closures` | `submit_governance_closure`; inspection | Immutable advisory task/initiative verdict and evidence; does not establish or rewrite user-work execution outcome |
| Closure confirmation | Derived from closure persistence plus intended inspection | `submit_governance_closure` result | `closure_confirmation` with `inspection_status`, `reason`, and bounded `attempts`; transient uncertainty is disclosed and never becomes execution state |
| Ordered chronology | `timeline` | Every semantic mutation; scoped reads | Canonical sequence ordering for incremental inspection |
| Retry records | `idempotency` | Mutations | Operation/key payload digest and original result; private retry integrity state |
| Projection queue and metadata | `projection_jobs` and projection metadata | Semantic mutations enqueue; best-effort host materializer and returned `human_view` status | Canonical scheduling/freshness metadata for derived views; no filesystem failure can roll back a valid ledger mutation |
| Human-readable plan/report views | Private `tasks/<task_ref>/` directory beside the V12 shard | Best-effort materializer; coordinator publication after current verification | Disposable structured Markdown for current/immutable plans and finalized reports only; task, decision, delegation, initiative, closure, governance, handoff, index, and timeline data remain SQLite-only; never canonical state, a recovery input, or native worker instructions; full IDs remain in SQLite/rendered evidence and no write occurs under `project_root` |
| Maintenance backups | Private `backups/<task-id>/<backup-id>/cortex.db` plus `manifest.json` beside the V12 shard | Explicit `v12_maintenance backup`; offline restore; explicit retention | Sealed whole-project-shard copy anchored to one task; owner-only and potentially contains every canonical record in the shard; never a task-only export |
| WAL/SHM | Adjacent SQLite files | SQLite | Database machinery, never report evidence or lifecycle authority |
| Bundled skills and profiles | Plugin package | Coordinator/worker model context and source lint | Authoritative orchestration policy plus advisory roles, not mutable task state |
| Project/feature docs | Repository `docs/` | Technical writers and humans | Source-backed navigation, never runtime authority |
| Private error log | Same-user Codex logs | Runtime diagnostics and bounded local inspection | Sensitive diagnostic state; never commit or paste raw |

## Schema integrity

An existing database is accepted only when its V12 application ID, schema
version, complete ordered additive migration history through
`v12-effective-outcome-coverage`, and stored project hash match. New bootstrap is
transactional. Normal writes use SQLite transactions and WAL so concurrent
reports, user decisions, assessments, initiative revisions, and projection-job
enqueue operations remain atomic and ordered.

The one additive pre-release V12 migration runs automatically on the first
normal, path-bearing `create_task` open of the exact released pre-human-view
V12 layout. In one transaction it verifies the legacy shard/root metadata,
adds the missing task-root binding, preserves every existing row, and wraps
each legacy report body as one finalized canonical chunk. The retained legacy
non-null report-header field receives only an inert compatibility value for
new chunked reports; canonical report evidence remains in immutable chunks.
Unknown or future layouts fail closed, and V11 is never opened or imported.

The same additive history creates the effective-contract revision, item,
assignment, and coverage tables. Bootstrap materializes revision 1 from the
immutable task contract. A later user `steer` decision creates a new effective
revision, retiring only named active items and adding only the stated
replacements; it does not rewrite the immutable original task contract or
unaffected coverage.

The state, project-shard, task, and view directories are created or reconciled
to `0700`. Before every SQLite open, symlink and non-regular database paths are
rejected; the database plus adjacent WAL/SHM files and generated Markdown are
reconciled to `0600`. Do not relocate a database to another project hash or edit
metadata to force adoption.

## Reference integrity

Known task, parent-delegation, input-report, linked-task, and linked-report
references must resolve in the current project ledger. Cross-project references
are rejected without destination mutation.

An ordinary-chat user decision must name an existing in-scope task, delegation,
plan, report, or same-project initiative. Plan/report decisions bind to the
exact immutable report digest; only plan `approve` additionally records the
current ready approval-view digest/source sequence and opaque handle. Plan
`request_revision` and `cancel` retain the exact plan digest and response
without that volatile view binding, so unrelated timeline events cannot block
feedback. Supersession must preserve subject identity. The backend verifies
scope and binding but does not authenticate a user or turn the record into
authorization.

Initiative dependencies are different: a missing target is retained as an
unresolved same-project relationship and returned with a warning. A cycle is
also retained with a warning. These warnings are evidence for model reasoning,
not storage corruption or lifecycle rejection.

## Idempotency

Every mutation requires a caller-generated operation-scoped idempotency key. Its
exact key is retained for
returns the server-issued key, and a normalized payload digest is stored with
the original JSON result. An exact replay returns that result and marks it
replayed; different content for the same operation/key
returns a non-mutating conflict.

Idempotency protects retries. It is neither authentication nor proof that
native work, external effects, or verification commands occurred.

The ledger also cannot prove that the root coordinator respected its
orchestration-only boundary. Project discovery, source/code/configuration
reads, analysis, edits, commands, tests, and verification must be attributable
to worker delegations and reports. The only coordinator project-read exception
is bounded orchestrator-owned knowledge routing through applicable instructions,
the project/feature indexes, and task-relevant linked pages. Coordinator
synthesis is otherwise limited to user input and ledger/report evidence. The
route uses only non-shell direct reads of already-known exact paths; discovery
through shell/search/graph and project-local state/artifact checks, including
`.codex` existence or unchanged-state, remain worker-owned.

Task instructions, delegation contracts, report content, decision
normalizations, and derived view source content are English. Exact user text is
preserved in explicitly labelled `*_original` fields. Localized coordinator
messages are a delivery layer and never replace durable evidence.

## Governance projection

Mode assessments and closures are append-only. The latest user override remains
the effective mode across later model assessments; with no override, the latest
model assessment is effective. Initiatives use a current row plus append-only
revisions, whose immutable payloads remain audit history.

No mode, status, warning, closure verdict, or missing record is an
authorization datum. Core tools must not consult governance storage to decide
whether coordination is allowed.

The execution projection and advisory closure projection are deliberately
separate. `execution_outcome` contains `evidence_status`
(`finalized_reports_present` or `no_finalized_reports`),
`finalized_report_count`, `completed_report_count`, and `outcome`. The finalized
count covers every finalized report. The completed count covers semantically
valid canonical finalized results with status `completed`; `outcome` is `null`
until any semantically valid canonical result is finalized, then reflects the
latest such result as `completed` or `incomplete`. This does not represent
native lifecycle state.
It remains independent of any advisory record. After sufficient evidence, the
coordinator automatically attempts the closure and intended bounded inspection.
One same-idempotency retry is allowed for a verified transient persistence or
inspection failure; a remaining
`closure_confirmation.inspection_status=unconfirmed` reports bookkeeping
uncertainty without changing the execution projection.

The final documentation-impact decision is model-owned rather than a storage
gate. Material impact is represented through documentation worker/verifier
delegations and reports; no impact is recorded as a report-grounded
`documentation not required` rationale. Missing documentation evidence may
lead to rework, replacement, or disclosed risk, but never backend prohibition.

Plan review, clarification, pause, revision, cancellation, and user-decision
interpretation are coordinator-owned ordinary-chat policy. Storage preserves
evidence and exact bindings; it does not create an approval gate, a pause state,
or user authorization.

## Derived human-readable task views

The canonical database may best-effort materialize only user-facing plan and
finalized-report Markdown views at the following exact layout. It never writes
any database, report, decision, projection, or `.codex` state below the target
`project_root`.

```text
~/.codex/cortex/v12/projects/p-<hash>/
└── tasks/<task_ref>/
    ├── plans/
    │   ├── current.md
    │   └── revisions/<plan-report-id>.md
    └── reports/<report-id>.md
```

Plan revisions and reports are separately addressable structured human-readable
documents. Task, decision, delegation, initiative, closure, governance,
handoff, index, and timeline data remain SQLite-only. Direct local edits are
preserved as `conflict`, not overwritten.
Projection materialization is nonblocking: its states are `ready`, `stale`,
`conflict`, `unavailable`, and `disabled`. Only `ready` may return a path, after
the regular non-symlink file, digest, containment, and current source-sequence
checks pass. A returned `ready` path may be linked only with a localized summary
and effect/next step; a non-ready state does not block canonical evidence or a
final answer.

## Retention and privacy

The ledger is local and retained until the user removes it through an explicit,
carefully scoped action. Do not include secrets, credentials, personal data, or
unnecessary raw operational output. Reports and diagnostics should be treated as
private even when they contain no obvious secret.

Do not commit or attach V12 databases, WAL/SHM files, diagnostics, prompts,
reports, or private task exports. Documentation may cite sanitized report IDs
or summarized evidence only when materially useful.

The operator maintenance CLI never prunes canonical task data. Projection prune
can remove only exact registered non-ready Markdown after validating the whole
candidate set. Backup retention can remove only 1–20 explicitly named complete
manifest-bound bundles and defaults to dry-run. There is no automatic cleanup.
Backup creation covers the whole project shard; restore requires all normal MCP
access to be stopped and explicit `RESTORE`, exact task/shard, and
`MCP_STOPPED` acknowledgement. That acknowledgement is not a shared lock.

## Historical compatibility boundary

V11 databases stay in their prior namespace and remain byte-for-byte untouched.
Schema v1 is fresh V12 state, not an in-place V11 migration. V11 tools and
unfinished V11 tasks are incompatible and never form a fallback identity or
recovery source.

## References

- [orchestration ledger](../features/orchestration-ledger/index.md)
- [advisory governance](../features/advisory-governance/index.md)
- [human-readable task views](../features/human-readable-task-views/index.md)
- [operator maintenance](../features/operator-maintenance/index.md)
- [security policy](../../SECURITY.md)
- [verification](verification.md)

<!-- GENERATED:END -->
