# Storage classification ADR

Status: implemented source-tree policy (schema v7).

## Decision

`cortex.db` is the only authoritative mutable store for new `cortex/v8` tasks.
SQLite is the only atomic boundary. Files under a task directory are either
(a) a required capability file, or (b) a disposable, reproducible projection
for humans, Desktop, or compatibility. A filesystem write must never be
described as part of the same transaction as a SQLite commit.

The runtime must create directories only when a required file or an explicitly
requested projection is materialized. It must not create a fixed report or
planning layout merely because a task exists. Legacy v7/v3 task files are
unsupported input: they are not imported, resumed, repaired, or deleted.

This ADR records the implemented SQLite-authoritative boundary. It describes
the workspace source; it is not evidence that a separately installed plugin
has been updated.

## Classification matrix

| Data or path | Canonical owner | Creator / reader | Required or optional | Rebuild, retention, security, completion relevance |
| --- | --- | --- | --- | --- |
| `cortex.db` | SQLite schema (`tasks`, revisions/state, plans, attempts, operations, receipts, documents, findings, blobs, logical artifacts, exports, tombstones, manifests, audit data) | Runtime creates/updates; runtime and scoped APIs read | Required | Rebuild is not applicable; retain for active and retained completed tasks; private ledger root and mode `0600`; all state transitions and completion proof depend on it |
| `cortex.db-wal`, `cortex.db-shm` | SQLite engine, not Cortex domain data | SQLite creates/reads; runtime must not treat either as a record | Incidental while WAL is active | SQLite lifecycle controls them; retain only as engine requires and never copy, parse, prune independently, or use for completion; inherit private directory controls |
| `.state.lock` | Runtime advisory coordination, not task state | Runtime creates/reads during serialized access | Required only while coordinating access | Re-creatable and disposable after no holder remains; mode `0600`; never an audit record or completion input |
| `tasks/<task>/delegations/*.briefing.md` | SQLite artifact catalog plus immutable exported briefing bytes; the file is the host capability | Runtime creates once per dispatch; worker reads exactly the granted file (or scoped paged fallback) | Required for each native dispatch | Not rebuilt in place: digest/path/permissions must remain stable; retain with the attempt until task cleanup policy permits; private regular file (`0400`), no symlink; worker acknowledgement is mandatory close evidence |
| `tasks/<task>/reports/records/*.json` | SQLite report/artifact record | Runtime projection writer; humans/Desktop may read | Optional projection | Regenerable from validated SQLite content; retain only as an audit/Desktop view under task retention; private atomic file; never authoritative and never sufficient for completion |
| `tasks/<task>/reports/markdown/*.md` | SQLite report/artifact record | Runtime projection writer; humans/Desktop read | Optional projection | Regenerable and deletable without changing state; private atomic file; links are convenience only; completion uses structured report/evidence in SQLite |
| `tasks/<task>/reports/receipts/*.json` | SQLite receipt/state rows | Runtime projection writer; humans/Desktop read | Optional projection | Regenerable; no independent retention or completion authority; private atomic file |
| `tasks/<task>/reports/consumptions/` | No canonical owner; consumption is a SQLite task document/receipt | No normal creator | Obsolete layout | Absence is normal; do not infer missing consumption from directory state; completion reads SQLite receipt state |
| `tasks/<task>/reports/delegations/` | No canonical owner; delegation index is SQLite task document/artifact metadata | No normal creator | Obsolete layout | Absence is normal; do not confuse with `tasks/<task>/delegations/`, which contains required briefings |
| `tasks/<task>/reports/` and its `records`/`markdown`/`receipts` parents | Projection namespace only | Created lazily by projection writer | Optional | Rebuildable; prune with projections; private directories; their existence is not task readiness, gate status, or completion proof |
| `tasks/<task>/planning/...` (manifest, overview, revision/package JSON) | SQLite planning document/artifact rows | Runtime writes projections after planner report; humans/Desktop read | Optional projection | Rebuildable on demand from the canonical planning artifact; retain while useful under task retention; private; planner report and structured plan, not Markdown, drive gates |
| `tasks/<task>/journal.md` | Best-effort human-readable lifecycle projection; SQLite audit/event data is canonical | Runtime appends; humans read | Optional telemetry/projection | Re-creatable/append-only view, may be incomplete; private `0600`; not used to authorize, resume, prune, or complete a task |
| `tasks/<task>/handoffs/`, `evidence/` and other fixed empty parents | SQLite rows/artifacts (where applicable) | Created only by an authorized projection when needed | Optional projection namespace | Absence must not mean absent state; no completion authority |
| Project `tasks/<task>/` directory and artifact path | SQLite task row's `artifact_dir` plus required briefing/projection children | Runtime allocates task directory; scoped readers use the exact task | Required as the namespace for task-scoped capability/projections | Private `0700`; retain with task policy; never scan old layouts to reconstruct state; task status/completion is SQLite-derived |
| Pre-SQLite v7/v3 JSON, Markdown, journals, and old directories | None in v8 | The normal runtime never reads them; explicit legacy maintenance may inventory/archive them | Unsupported legacy input | Do not migrate, resume, or repair; it has no security or completion authority. Deletion is allowed only after a verified archive and archive-specific confirmation |
| `projection_jobs` and `prune_tombstones` | SQLite outbox/maintenance rows | Projection service and confirmed prune route | Required durable workflow state | Jobs are leased and acknowledged only after digest-verified materialization; tombstones retain canonical task state until filesystem removal succeeds |
| Project/plugin source, tests, docs, and configuration | Git working tree / release archive | Developers and validators create; runtime reads selected executable configuration | Required product source, not task data | Retained by repository policy; normal source permissions; docs are navigation/prior knowledge, never proof of runtime behavior or completion |

## Why the briefing file is different

The immutable briefing is not a redundant report export. The native worker
bootstrap grants one exact path and SHA-256 digest, and the worker must read
that file before project work. The host may be unable to read a project-local
file; in that case `read_dispatch_briefing` is an identity- and digest-scoped,
bounded fallback. The worker cannot enumerate `.codex/cortex` or select another
briefing. `record_report` rechecks the exact file, digest, regular-file type,
private read-only permissions, and the required `Dispatch briefing reviewed:`
evidence marker. Therefore the briefing export is a required capability for an
attempt even though its content is also catalogued in SQLite.

Reports, receipts, plans, journals, and indexes do not have this capability
property. Their SQLite records are authoritative and their files can be
regenerated. Atomic rename and fsync make one projection durable and resistant
to partial writes; they do not make several filesystem writes and a SQLite
commit one transaction.

## SQLite and sidecar handling

The runtime enables WAL and `synchronous=FULL`. WAL permits independent readers
while a coordinator writes; `-wal` and `-shm` are SQLite-managed working files,
not application artifacts. They must never be included in a task manifest,
treated as projections, copied as backups independently, or used as evidence.
Backups and pruning must use SQLite-aware procedures and the project-ledger
lock. The advisory `.state.lock` similarly coordinates access but contains no
authoritative state.

## Markdown, legacy layout, and directory creation

Markdown and JSON are human-facing projections. Documentation and projection
links help workers navigate prior knowledge, but consequential claims must be
confirmed in current source, tests, schemas, migrations, or executable
configuration. A Markdown conclusion cannot override structured findings,
open P2 work, or server-observed close requirements.

Task creation records only SQLite state and a task-scoped namespace. Report,
planning, handoff, evidence, and question parents are created only when their
authorized projection is materialized. `report_bus_paths()` and its equivalent
helpers validate paths without creating them. The runtime must not remove or
rename `tasks/<task>/delegations/`: it is the namespace for immutable briefing
capabilities when a dispatch needs one.

## Implemented storage workflow

Migrations are append-only through v7 and run in one SQLite transaction with a
content checksum. Canonical writes first commit content blobs, logical-artifact
metadata, export authorization, and an outbox job. Projection materialization
then claims a lease, writes a private regular file by atomic replacement and
`fsync`, verifies its digest, and acknowledges the job separately. A failure
leaves retryable canonical state. Required briefings add a hard capability
check; optional reports, receipts, plans, and journal outputs may be rebuilt.

Prune similarly records a tombstone before any filesystem operation. It never
deletes WAL/SHM independently; after safe projection removal, it finalizes the
task graph in SQLite. Review and close status derive from structured findings
and observed verification, not from Markdown. The explicit legacy lifecycle
is deliberately outside the active ledger and never imports legacy state.

## Evidence consulted

- `plugins/cortex/scripts/cortex_runtime/ledger_db.py`: SQLite schema,
  migrations, WAL setup, private root, and artifact catalog.
- `plugins/cortex/scripts/cortex.py`: task initialization, lazy path
  validation, prune coordination, briefing validation, and task paths.
- `plugins/cortex/scripts/cortex_runtime/projection_service.py`: durable
  outbox claiming, authorization, materialization, verification, and retry.
- `plugins/cortex/scripts/cortex_runtime/health_maintenance.py` and
  `legacy_lifecycle.py`: SQLite-aware maintenance and explicit legacy archive
  lifecycle.
- `docs/project/decisions.md`, `verification.md`, and `gotchas.md`: v8
  authority, projection, migration, pruning, and documentation contracts.
- `tests/test_ledger_db.py` and `tests/test_cortex_control.py`: current
  SQLite, briefing, report-bus, and path invariants.
