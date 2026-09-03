# Human-readable task views

<!-- GENERATED:START -->

## Purpose and authority

Cortex 1.15.0 projects selected host-private plan and finalized-report evidence
into human-readable Markdown views. These views make plan/report content easier
for a coordinator and user to inspect; they do not create another ledger or
alter the execution model. Other task records remain SQLite-only and are read
through bounded inspection tools.

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
    ├── plans/
    │   ├── current.md
    │   └── revisions/<plan-report-id>.md
    └── reports/<report-id>.md

~/.codex/cortex/views/
├── plan-<content-sha256>.md
└── report-<content-sha256>.md
```

Only plan and report links are materialized as user-facing Markdown. `current.md`
is the current plan link, and each immutable plan revision and finalized report
has its own identity-addressable document. Task, decision, delegation,
initiative, closure, governance, handoff, index, and timeline records remain
SQLite-only; they are available through bounded inspection tools and are not
published as Markdown projections.

After verifying the shard-local source, Cortex creates a byte-identical
content-addressed alias under `~/.codex/cortex/views/` and returns that shorter
absolute Markdown link. The filename contains the complete content SHA-256;
an existing alias with different bytes fails closed. This prevents a model from
having to reproduce a path containing the project hash, task reference, and
canonical report identifier while keeping all files host-private.

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

Only `ready` may include a file path and a server-formatted `markdown_link`, and
both identify the same absolute verified artifact. Copy `markdown_link`
byte-for-byte into user-facing messages; never reconstruct a link from compact
refs. Cortex does not return a guessed, relative, stale, missing,
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
than caller-supplied export paths. Generated files are ordinary, readable
Markdown: plans and reports are structured human-readable documents with
labeled headings, normal lists, and paragraphs, not raw nested field dumps.
The renderer owns the standard document sections and typed blocks, while
ordinary authored Markdown remains Markdown. Backticks, lists, links, emphasis,
HTML, tables, blockquotes, rules, and fences are preserved without backslash
escapes or character entities. The optional `cortex/report-view/v1`
envelope is parsed only at render time; malformed, unknown, or legacy content
uses the safe generic fallback without changing canonical report acceptance or
persistence. Structured JSON is canonical database data only; it is never dumped as a JSON
object, script block, `<pre>` block, entity-encoded payload, or opaque blob into
a human view.

Human views are disposable host-private presentation artifacts. Authored
formatting can affect only that derived document; it is never parsed back,
treated as an instruction, or used as ledger, approval, identity, or recovery
authority. Raw files and host previews therefore contain the authored Markdown
instead of renderer-generated escape syntax.

The view writer intentionally preserves direct edits. If the on-disk content no
longer matches the derived artifact that Cortex last verified, it records a
`conflict` rather than replacing the user's change. Such a file remains private
host data, but it is not presented as canonical evidence.

Projection work is coordinated with leases and supersession. A lease prevents
simultaneous materializers from treating the same work item as theirs; a newer
canonical source sequence supersedes older queued or running work. This keeps a
slow plan or report projection from publishing an old snapshot after later
mutations.
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

The terminal coordinator evidence page carries `human_view` for the selected
active plan and `human_views` for the complete selected finalized evidence set.
Each ready entry contains the exact verified `markdown_link`; private report
identity and construction details remain absent. The transport also places
these links before the serialized evidence body so CLI and Desktop hosts expose
the same link even when they primarily surface text content. A paginated read
does not publish the set until its terminal page, preventing an incomplete
evidence read from being presented as complete.

Successful `close_task` repeats the verified links for every finalized plan and
report. The immediate final response copies those current server-returned links
byte-for-byte and does not reconstruct a path remembered from an earlier read.

For plan review, the link and localized summary are rendered in the
coordinator's final answer after the durable review hold opens. The tool call
does not display its stored prompt to the user. The summary must be sufficient
for an informed approve/revision/cancel decision and covers scope, ordered
stages, intended changes, verification, stop conditions, and material risks or
unresolved items. A generic “plan ready” question is not a substitute for the
verified link and decision-ready summary.

It must never publish a bare path, a stale link, or an unverified link. A link
means the coordinator has confirmed the matching `ready` response and used the
exact absolute path it returned. The accompanying summary is localized to the
user's language and explains what evidence the particular page contains.

The published form is the server-provided `markdown_link` field copied
byte-for-byte, including its readable label and absolute destination:
its readable label and destination are already bound to the verified artifact.
Cortex never uses a bare or backticked path, a code block, a constructed path,
or a line break inside the destination.

If a requested plan or report view is `stale`, `conflict`, `unavailable`, or
`disabled`, the coordinator says so in the user's language and summarizes the
canonical SQLite evidence inline. The user still receives the substantive task
state; view delivery is an aid, not a gate on planning, acceptance, decisions,
or completion.

## Task inspection and closure bookkeeping

Task, delegation, governance, closure, decision, and timeline records are not
rendered as Markdown views. Use scalar `read_state` for current status,
`read_evidence` for selected finalized reports and their verified links, and
`read_timeline` only for explicit newest-first history. Execution status derives
deterministically from current effective-contract coverage and makes no
native-lifecycle claim. Closure record status and latest verdict remain
separate scalar fields. Neither is a native-host lifecycle signal.

`read_scope` exposes one responsibility's outcome-keyed coverage rows. Each row
repeats the exact current semantic `outcome` and binds it to coverage
`status`/`reason`, `ownership`, and `delivery_assignability`. Ordinary delivery
uses only `assignable` outcomes. `loss_recovery_only` means one nonterminal
owner still exists and requires independently confirmed loss plus the atomic
successor path; `not_assignable_terminal_owner` means finalized immutable
ownership. Row ordering carries no identity or routing meaning, so steering can
add, retire, or reorder outcomes without making positional inference safe.
Admission remains authoritative and rejects an inconsistent selection without
mutation even when a caller ignores the projection.

After sufficient completed evidence, the coordinator selects `ready`,
`ready_with_risks`, or `not_ready`, attempts the advisory closure, and inspects
the intended record automatically. `ready_with_risks` is not a request for
user confirmation. The closure result's `closure_confirmation` reports
`inspection_status` (`confirmed` or `unconfirmed`), the exact reason
(`record_inspected`, `persistence_unavailable`, `inspection_unavailable`, or
`record_not_observed`), and `attempts` (1 or 2). At most one same-idempotency
retry is made for a verified transient persistence or inspection failure. An
unconfirmed advisory record is disclosed as bookkeeping uncertainty while the
independent `execution_outcome` remains intact; advisory-view availability does
not alter execution evidence.

Durable worker-authored fields and generated view content are English. Existing
task original user language is retained in explicitly labelled `*_original`
fields. Decision records retain exact `response_original` without an English
duplicate. Canonical
product-facing report/handoff payloads may instead carry one optional
unchanged `source_text` value, rendered once as inert source material without
a language tag or translated/original duplicate. The localized coordinator
message is a delivery layer and does not replace canonical evidence.

## Plan review and user decisions

Plans are represented by plan reports and their revisions. The plan view shows
the plan report type, immutable report identity, predecessor when present,
content digest, and whether review is `informational` or `required`.
`plans/current.md` identifies the current
reviewable plan; immutable historical revisions appear at
`plans/revisions/<plan-report-id>.md`. A revision's digest binds the displayed
content to the canonical report so review cannot silently drift to altered
text.

The matching narrow decision record operation records `approve`, `reject`, `request_revision`,
`clarification`, `cancel`, `accept_risk`, `override`, or `steer` against an exact task,
plan, initiative, delegation, or report. Its closed canonical request preserves
the task/subject refs, decision type, neutral `prompt`, exact
`response_original`, and `user_language`; `subject_digest` is included for
plan and report subjects only. For plan and report decisions, the decision is bound to the canonical
`sha256:<64-lowercase-hex>` subject digest; a plan must be finalized and
completed. Only `decision_type=approve` requires the exact server-issued
`approval_handle` from a current ready `approval_view`, plus the matching plan
digest, view digest, and view source sequence. A ready plan read also exposes
`handles.decision_binding` with those existing decision-input names. The
`request_revision` and `cancel` decisions preserve the exact finalized plan
digest and user response but do not require a volatile approval-view binding;
intervening non-plan timeline events therefore do not block saving that
feedback. The approval relation is stored only after Cortex has verified the
exact current view; it is relational proof of a ready review snapshot, not a
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

Private/internal publication storage may use an explicit assembly lifecycle:

```text
private begin → append* → finalize
private begin → abort
```

Large evidence publication is immutable and bounded by the active semantic
publication contract. The three worker-only publication operations emit the
appropriate plan, result, or documentation evidence; `read_task` exposes the
server-produced task evidence view with private ledger identity removed and
supports only its server-owned bounded continuation. It never exposes partial
evidence as completed evidence. The matching human-readable view is likewise
derived from canonical complete evidence.

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
- separate `execution_outcome` and `advisory_closure` task-inspection
  projections, automatic closure inspection after sufficient evidence,
  bounded retry, and disclosure of `closure_confirmation`=`unconfirmed`
  without changing neutral execution evidence; and
- a Russian-user Luna/high scenario in which durable artifacts remain English,
  coordinator publication is localized, verified clickable links are used when
  ready, canonical evidence is summarized inline otherwise, and the project
  receives zero generated writes.

See [project verification](../../project/verification.md),
[storage classification](../../project/storage-classification.md), and the
[orchestration ledger](../orchestration-ledger/index.md).

<!-- GENERATED:END -->
