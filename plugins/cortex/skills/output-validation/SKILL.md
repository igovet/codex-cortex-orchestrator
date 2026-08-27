---
name: output-validation
description: Internal Cortex v12 validation overlay. Load for an explicitly activated Cortex task when a result or closure recommendation needs acceptance and evidence review.
---

# Output Validation

Validate the result, not the confidence of its author. The coordinator designs
the evidence request and delegates project verification; a validation worker
performs every project inspection and executed check. The coordinator never
reads project artifacts, runs a command, or performs substantive verification
itself.

1. Restate acceptance criteria as observable checks and create bounded
   validation delegations. Compose the orchestrator's authoritative delegation
   knowledge contract into each worker's instructions.
2. Have workers inspect the relevant change and execution path for scope and
   unintended effects.
3. Have workers run the smallest relevant build, typecheck, lint, test,
   reproduction, or behavioral check; use an independent worker where risk
   justifies it.
4. Reconcile reported files, commands, outputs, and observed behavior by report
   ID. If the evidence is insufficient, delegate the missing check.
5. From worker reports, classify each criterion as passed, failed, blocked, or
   unverified, with its evidence, risk, and useful next owner.
6. Preserve a concise verified change-surface summary for the coordinator's
   later documentation-impact decision, covering behavior, architecture,
   interfaces, commands, verification, conventions, and feature ownership.

Artifact verification includes existence, absence, unchanged-state, manifests,
Git, caches, worktrees, and project-local `.codex`. A validation worker performs
every such check; the coordinator never performs one directly.

When validation finds a defect or material concern, use a structured advisory
finding in report content: stable finding key, severity, affected surface,
sanitized evidence, failure scenario/impact, recommendation, disposition, and
any exact decision reference. Preserve conflicting findings and supersede them
explicitly; do not turn severity, waiver, or resolution into a backend gate.

For an executed check, the worker records the command, working directory,
integer exit code, and decisive sanitized evidence in a report. A passing check
has exit code 0. A negative-path check uses an assertion harness that observes
the expected failure and itself exits 0. Never omit or relabel a nonzero result.

Missing evidence is not a pass. It may justify more verification, bounded
rework, `ready_with_risks`, or `not_ready`; it never becomes a backend
permission barrier or prevents an honest final answer.

Record decisive check evidence in the immutable report, including selected
large-report sections when chunking is required. A status or final handoff may
summarize that evidence only after the relevant report sections were read.
When a current contained digest-verified report/status Markdown projection is
available, publish its clickable absolute path with a localized result summary
and implication. A missing or stale projection produces no link and does not
change the acceptance classification.

Project verification precedes the conditional documentation stage. The
coordinator uses bounded knowledge-route context and these worker reports only;
it delegates any documentation inspection, synchronization, and material
independent verification before advisory closure or the final answer. When the
task has an initiative, validate the durable close handoff: initiative closure
first, then a distinct task-subject closure anchored by the exact task_ref. Do
not accept a final claim until task inspection reports task_closed and the task
closure verdict.
