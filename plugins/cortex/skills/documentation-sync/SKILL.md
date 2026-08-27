---
name: documentation-sync
description: Internal Cortex documentation overlay. Load only after verified changes in an explicitly activated Cortex task require durable project or feature documentation updates.
---

# Documentation Sync

Update only durable knowledge changed by completed, verified work.

The coordinator must not read the target diff, source, tests, or documentation,
and must not edit or verify documentation directly. It routes existing report
evidence and delegates every missing inspection, technical judgment, edit,
command, artifact/state check, and verification action. This includes proving
that expected documentation or project-local artifacts are absent or unchanged.

After project verification, the coordinator makes the documentation-impact
decision from its bounded index-driven knowledge-route context and verified
implementation and verification reports. This is a model-owned outcome
obligation before closure/final synthesis, not a backend stage, gate, or
permission. Consider behavior, architecture, interfaces, commands,
verification, conventions, feature ownership, public usage, and operating
expectations. Do not inspect additional project files to fill an evidence gap.

When durable knowledge changed:

1. If a required index is missing or unreadable, or the routed knowledge and
   reports do not identify affected documentation, create a bounded discovery
   delegation. A reported stale, conflicting, or incomplete page receives
   follow-up only when its task impact warrants it; do not force a harvest.
   The coordinator does not inspect source or additional documentation itself.
2. Create a dedicated documentation-sync worker, normally using the advisory
   `technical_writer` profile. Compose the orchestrator's authoritative
   delegation knowledge contract into its instructions with the relevant
   report IDs and a concise textual scope boundary.
3. Assign every affected surface among the harvest documents in
   `docs/project/` and `docs/features/` plus affected public documentation.
   Do not edit unrelated pages simply to demonstrate that synchronization ran.
4. Instruct the writer to preserve manual text and update only generated blocks
   unless the task explicitly authorizes a manual-document change.
5. For material documentation impact, create a separate verification
   delegation. That worker verifies documentation against current source,
   tests, commands, links, feature ownership, and the change reports, then
   publishes the evidence by report ID.
6. Use writer and verification reports to decide whether corrective
   documentation work or a residual-risk disclosure is needed.
7. When the active tool returns a current contained digest-verified final
   report or plan Markdown projection, give the user its clickable absolute
   path with a localized summary of what changed and verification status. Never
   publish a bare, guessed, or stale path. Task, decision, delegation,
   initiative, closure, governance, handoff, index, and timeline records are
   SQLite-only and have no user-facing Markdown path. Projection failure is a
   human-view limitation, not evidence that documentation work failed.

For the `documentation not required` path, when the reports establish that no
durable documentation surface changed, do not create a writer merely to record
that the task occurred. Require one
finalized worker-owned report with an explicit English documentation-impact
section and material/no-impact rationale. An existing implementation or
verification report qualifies only when it already contains that explicit
section. When the evidence is absent or spread across reports, the coordinator
creates a bounded evidence-synthesis/documentation-impact delegation, passes
the exact report IDs, waits, and reads the worker-submitted finalized rationale
report; the coordinator never calls `submit_report` or self-asserts
`documentation_not_required`. The orchestrator links the exact task, that
documentation-impact report ID, and every other required report in the final
initiative, then cites those exact report IDs and returned digests in closure
`evidence` before task-scoped and initiative-scoped governance inspection.
For light/full governance, missing or unconsumed required documentation-impact
records reject closure, while still never requiring a documentation edit for a
no-impact report and never blocking an honest user-facing explanation of the
limitation.
