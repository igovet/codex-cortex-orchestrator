# Human-readable task views

<!-- GENERATED:START -->

## Purpose and authority

Cortex 12.0.0 projects selected host-private task evidence into
human-readable Markdown views. These views make a task easier for a coordinator
and user to inspect; they do not create another ledger or alter the execution
model.

The canonical record is always the task's SQLite database:

```text
~/.codex/cortex/v12/projects/p-<hash>/cortex.db
```

The database is the sole authority for task state, plans, delegations, reports,
decisions, ordering, and source sequences. Every Markdown artifact is
disposable derived output: Cortex never parses it back into SQLite, derives
authority from it, or treats it as a recovery input. In particular, a delegation
view is not native worker input; the coordinator obtains the native worker brief
from the canonical delegation record.

Views are host-private. Cortex writes neither these artifacts nor any other
projection file below `project_root`; a task may therefore have useful views
without leaving generated task material in the target project.

## Location and layout

Views share the canonical project's private shard. For a compact task reference
represented as `<task_ref>` (`t_<12-hex>`), the layout is exactly:

```text
~/.codex/cortex/v12/projects/p-<hash>/
└── tasks/<task_ref>/
    ├── index.md
    ├── task.md
    ├── plans/
    │   ├── current.md
    │   └── revisions/<plan-report-id>.md
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

The timeline is partitioned into pages of 100 events. Its index identifies the
available first/last-sequence boundaries, event counts, relative paths, and
latest sequence; it is not an unbounded duplicate of task history. An empty
chronology creates only `timeline/index.md`, with no empty page.

`index.md` is the compact entry point, while `task.md` provides the task-level
summary and current references. Plan, delegation, report, and decision pages
are separate identity-addressable views so a coordinator can link a particular
piece of evidence without making an entire task history the link target.

Canonical full task IDs remain in SQLite and rendered evidence, but never in a
user-facing projection link path. When an existing V12 shard has the released
`tasks/<task-id>/` directory, materialization first performs one atomic
no-replace rename to `tasks/<task_ref>/`. If the compact directory already
exists or either directory is unsafe, Cortex preserves both directories, does
not merge or delete files, and reports a non-ready conflict or unavailable view.

## Projection lifecycle

Every semantic SQLite mutation atomically enqueues the corresponding projection
job in the same database transaction. Committing the canonical mutation does
not depend on filesystem availability. Projection jobs are then materialized
best effort, so a permissions error, I/O failure, delayed writer, or host
shutdown can never reject, roll back, or otherwise block a valid ledger
mutation.

Mutation and inspection results can include a dynamic `human_view` result. Its
state is computed at response time and is never frozen inside an idempotency
reply; a replay rechecks the current file. The state is one of:

| Status | Meaning |
| --- | --- |
| `ready` | A current, verified view is available. |
| `stale` | A prior view exists but is not current for the canonical source sequence. |
| `conflict` | A direct local edit was preserved, so Cortex will not overwrite the view. |
| `unavailable` | No verified view can currently be offered, for example after a materialization failure. |
| `disabled` | Human-readable views are disabled for this task or host. |

Only `ready` may include a file path, and that path is an absolute path to the
verified artifact. Cortex does not return a guessed, relative, stale, missing,
or conflicting path as if it were clickable evidence. Before a view is exposed
as ready, Cortex verifies that it is a regular file rather than a symlink,
checks the stored digest, and confirms that its embedded/canonical
`source_sequence` still equals the current SQLite source sequence.

## Safe materialization and concurrent writers

A materializer creates the output through an atomic temporary-file, `fsync`,
replace, and read-back verification sequence. Directories use mode `0700` and
files use mode `0600`. The final path is rechecked as a regular non-symlink
file, then its digest and source sequence are verified before it becomes ready.
Every output path is derived from server-generated validated identifiers rather
than caller-supplied export paths. Arbitrary task/report/user content is
rendered as inert escaped JSON or clearly labeled text, so it cannot become a
trusted Markdown instruction, path, or projection directive.

The view writer intentionally preserves direct edits. If the on-disk content no
longer matches the derived artifact that Cortex last verified, it records a
`conflict` rather than replacing the user's change. Such a file remains private
host data, but it is not presented as canonical evidence.

Projection work is coordinated with leases and supersession. A lease prevents
simultaneous materializers from treating the same work item as theirs; a newer
canonical source sequence supersedes older queued or running work. This keeps a
slow projection from publishing an old snapshot after later task mutations.
The compact task `index.md` is rendered last so it never advertises a newer set
of dependent pages before those pages have been attempted.
Failure handling is nonblocking: later mutations and later projection attempts
may proceed even when one materialization attempt fails.

## Coordinator publication rules

The coordinator actively publishes a relevant verified clickable absolute link,
with a localized summary, whenever it is useful during:

- plan review;
- progress updates;
- report acceptance;
- decision recording; and
- the final response.

It must never publish a bare path, a stale link, or an unverified link. A link
means the coordinator has confirmed the matching `ready` response and used the
exact absolute path it returned. The accompanying summary is localized to the
user's language and explains what evidence the particular page contains.

The published form is a clickable Markdown link such as
`[Обзор задачи](</absolute/path/to/t_ref/task.md>)`: its readable label is
localized, and its destination is the exact returned verified path. Cortex never
uses a bare or backticked path, a code block, a constructed path, or a line
break inside the destination.

If a requested or relevant view is `stale`, `conflict`, `unavailable`, or
`disabled`, the coordinator says so in the user's language and summarizes the
canonical SQLite evidence inline. The user still receives the substantive task
state; view delivery is an aid, not a gate on planning, acceptance, decisions,
or completion.

Durable internal fields and generated view content are English. Original user
language is retained only in explicitly labelled `*_original` fields, with a
separate English `*_en` field. The localized coordinator message is a delivery
layer and does not replace that durable English evidence.

## Plan review and user decisions

Plans are represented by plan reports and their revisions. The plan view shows
the plan report type, immutable report identity, predecessor when present,
content digest, and whether review is `informational` or `required`.
`plans/current.md` identifies the current
reviewable plan; immutable historical revisions appear at
`plans/revisions/<plan-report-id>.md`. A revision's digest binds the displayed
content to the canonical report so review cannot silently drift to altered
text.

`record_user_decision` records `approve`, `reject`, `request_revision`,
`clarification`, `cancel`, `accept_risk`, or `override` against an exact task,
plan, initiative, delegation, or report. For plan and report decisions, the
decision is bound to the canonical `sha256:<64-lowercase-hex>` subject digest;
a plan must be finalized and completed. A plan decision additionally requires
the exact server-issued `approval_handle` from a ready `approval_view`, plus
the matching plan digest, view digest, and view source sequence. That
single-use relation is stored only after Cortex has verified the exact current
view; it is relational proof of a ready review snapshot, not a
host-authenticated user-turn receipt. The coordinator must therefore ask in
the user's language and wait for a new response; exact reuse of the original
task request is rejected, while arbitrary user prose is never semantically
classified by the backend. Its durable attribution is
`user_via_coordinator`: the coordinator transmits the user's decision but does
not impersonate the user as an independent source. Decision records and their
views are evidence only. They do not block an otherwise valid mutation, native
worker execution, report reads, or final synthesis. Clarification is not
approval, and a revised plan's new report ID/digest needs a new decision.

## Chunked reports

Large reports are stored and projected through an explicit lifecycle:

```text
single
begin → append* → finalize
begin → abort
```

`single` records one canonical JSON body up to 64 KiB. `begin` opens a stable
report ID, `append` adds sequential labeled complete-JSON chunks up to 32 KiB,
and `finalize` requires the exact chunk count and manifest digest. `abort`
retains an incomplete stream without pretending that it is complete. A report
is limited to 256 chunks and 8 MiB; assembling and retained task totals are
also bounded. `read_reports` applies optional section selection, an opaque
cursor, and a maximum 65,536-byte budget, and returns only whole chunks;
it does not expose a partial chunk stream as completed evidence. The matching
human-readable report view is likewise derived from the canonical complete
evidence and source sequence.

## Verification expectations

Verification covers the filesystem and the coordinator-facing contract, not
just Markdown rendering. In particular, tests and review should demonstrate:

- canonical state remains in the V12 SQLite database and no projection write
  occurs under `project_root`;
- the exact private layout, permissions, atomic write/read-back checks,
  non-symlink rule, digest, and current source-sequence verification;
- atomic mutation-plus-job enqueue, nonblocking materialization failure,
  lease/supersession behavior, and preservation of direct-edit conflicts;
- dynamic status handling that returns a path only for verified `ready` views;
- plan revision/digest/review semantics, evidence-only user decisions, and
  bounded reads of complete chunked reports; and
- a Russian-user Luna/high scenario in which durable artifacts remain English,
  coordinator publication is localized, verified clickable links are used when
  ready, canonical evidence is summarized inline otherwise, and the project
  receives zero generated writes.

See [project verification](../../project/verification.md),
[storage classification](../../project/storage-classification.md), and the
[orchestration ledger](../orchestration-ledger/index.md).

<!-- GENERATED:END -->
