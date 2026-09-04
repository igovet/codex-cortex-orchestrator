# Human-readable task views

## Authority and location

SQLite is the canonical record for contracts, graphs, assignments, observations,
decisions and terminal publications. Markdown views are disposable derived
presentation; they never become ledger, worker, approval or recovery input.

Views remain host-private, outside the task project:

```text
~/.codex/cortex/v12/projects/p-<hash>/
└── tasks/t_<12-hex>/
    ├── plans/
    │   ├── current.md
    │   └── revisions/<plan-report-id>.md
    └── reports/<report-id>.md

~/.codex/cortex/views/
├── plan-<content-sha256>.md
└── report-<content-sha256>.md
```

The shorter content-addressed alias is byte-identical to its verified source.
It avoids asking the model to reproduce long private paths. Existing aliases
with different bytes fail closed. Current storage accepts only its current
schema; there is no old task-directory migration or compatibility renderer.

Only plans and finalized reports have user-facing Markdown projections. Task,
graph, assignment, governance, decision, closure and timeline state stays in
SQLite and is exposed through purpose-specific bounded reads.

## Typed rendering

The renderer consumes the current terminal publication type: plan, result or
documentation. The assignment fixes that type before dispatch. Plans present
their complete candidate graphs and alternatives; result and documentation
views present the assigned node coverage and canonical verification evidence.
No legacy report envelope, generic fallback or duplicate verification source
is accepted as a current typed report.

Authored Markdown is preserved as presentation. It is never parsed back into
authority. Raw JSON payload dumps are not human decision packets. English
worker evidence and exact original user statements remain distinguishable;
the coordinator supplies the localized summary.

## Materialization and integrity

Canonical publication and its queued projection work commit atomically.
Derived filesystem IO follows that commit. A failed view write cannot remove
the report or create a second publication slot.

Files are written through temporary-file, fsync, atomic replacement and
read-back checks. Private directories use 0700 and files 0600. Paths derive
from server-owned identity, not user-supplied export destinations. Symlinks,
unsafe directories, digest mismatch and stale source sequence cannot produce
a ready link. Leases and supersession prevent an older concurrent writer from
publishing a stale snapshot.

Direct local edits are preserved as conflicts rather than overwritten.
Projection states distinguish ready, stale, conflict, unavailable and disabled.
Only a verified ready view exposes its absolute path and complete Markdown
link. Every response computes current availability; replaying a receipt does
not freeze an earlier filesystem state.

A post-commit projection failure currently returns a storage error. An
identical publication reconciliation repairs the derived view without another
report or mutation; a changed publication remains a conflict. This explicit
fault-injection path is tested separately from zero-error native qualification.
It must never be described as a clean first publication.

## Review and closure

The coordinator copies every relevant server-returned complete Markdown link
byte-for-byte, with its label, brackets and absolute destination. It never
reconstructs a link, presents a bare path, or labels an unverified file ready.

Opening plan review requires a current independently validated candidate and
a verified ready human view. Missing or stale derived views must be repaired
before the review packet opens. An inline summary may explain a limitation,
but cannot replace the required ready plan link or authorize approval.

After opening, the immediate localized packet includes the verified plan link,
scope, ordered stages, intended changes, verification, stop conditions, risks
and unresolved choices. Ordinary choices are approval, revision or cancellation.
A decision-bearing family presents its exact alternatives: one direct answer
may select and approve a branch atomically.

Review binds the current contract, candidate, independent validation, artifact
generation, reconciliation state and material governance evidence. Changes to
that boundary make an unanswered packet stale. Direct concrete steering is
recorded atomically without a second confirmation, including while an older
review is pending. The coordinator never supplies private decision handles.

Required approval is not task completion. Before closure, the coordinator
reconciles the latest result and evidence, presents its impact, checks,
documentation and unresolved risks, and opens a fresh closure review. Only
the direct close choice permits closure; revise keeps the same task open.
Success returns the current verified report/plan links for the final response.

## Purpose-specific reads

- Current scalar status comes from read_state, not liveness polling.
- read_scope exposes typed graph nodes, readiness and unmet prerequisites for
  one responsibility. Waiting nodes are not assignable, regardless of whether
  their names were observed. Bootstrap availability reflects actual admission.
- read_evidence returns selected finalized evidence with links on its terminal
  page; a partial page does not prove the complete evidence set was consumed.
- Recovery of unfinished delegated work uses read_continuations after current
  state, not historical timeline.
- read_timeline is reserved for a genuine chronology or audit need.

## Verification

Focused coverage includes current typed renderers, private paths and
permissions, symlink/conflict handling, digest and sequence checks, concurrent
supersession, post-commit repair, exact first-publication receipts, and
mandatory review/closure binding. Native CLI and Desktop qualification must
also observe the user-facing links; source tests alone do not prove host UI
delivery.

Relevant tests:

- tests/test_v12_projection_markdown.py
- tests/test_publication_projection_repair.py
- tests/test_replan_review_lineage.py
- tests/test_candidate_family_public.py
- tests/test_closure_review.py

See [verification](../../project/verification.md),
[storage classification](../../project/storage-classification.md),
[typed integrity contract](../../project/typed-orchestration-integrity.md),
and [ledger](../orchestration-ledger/index.md).
