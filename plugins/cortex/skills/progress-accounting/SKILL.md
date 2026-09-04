---
name: progress-accounting
description: Keep Codex task progress reporting concise and evidence-focused without collecting unnecessary token, model, or private telemetry.
---

# Minimal Progress Accounting

Track only task-relevant evidence: outcome, scope, delegations used, reports,
checks run, pass/fail/blocked/unverified status, material risk, and verified
human-view artifacts. Do not record token counts, hidden reasoning,
secret-bearing prompts, private native session data, raw diagnostics, or raw
conversation transcripts in repository files or ledger prose.

Retain the canonical project root and the server-issued task anchor when one
exists, the current
delegation knowledge contract, and evidence-backed documentation discrepancies
needed for later routing. Do not duplicate the knowledge pages or raw worker
output in accounting records.

The coordinator accounts from ledger state and worker reports only. It does not
inspect project content or rerun checks to improve the accounting record; any
missing project fact or verification evidence requires a bounded delegation.
It never checks Git, manifests, caches, worktrees, file existence/absence or
unchanged-state, or project-local `.codex` as an accounting shortcut.

A durable delegation may retain its coordinator-selected profile, model, and
reasoning effort as audit metadata. Do not duplicate that routing metadata as a
general productivity metric.

Prefer the task ledger and normal Git history for progress. Create persistent
metrics only when the repository explicitly needs them.

Send a user-facing progress update only when evidence changed the outcome,
plan, risk, accepted scope, completed work, verification state, or next useful
action. Follow the mandatory packaged `coordinator-communication` policy. Use
the language of the latest meaningful user message. Lead with the result,
state what changed and the evidence-backed user impact, and end with the next safe step.
Default-hide opaque IDs, ledger/governance jargon, private paths, raw
diagnostics, and raw worker output; disclose technical detail progressively and
use optional contextual humor only after the material fact when it is safe.
Suppress unchanged waits, repeated summaries, routine pagination, chunk
assembly, retry attempts, and internal worker-recovery chatter.

Progress accounting does not authorize stopping orchestration. Once sufficient
completed outcome evidence is available, the coordinator presents the reconciled result,
opens the mandatory closure review, records the user's explicit `revise` or
`close` choice, and calls `close_task` only after `close`, followed by supported
inspection of the intended record. Incomplete requirements or checks never permit
task closure, even after a close answer. Report non-completion and continue safe
in-contract repair; do not use an incomplete verdict to end the task.
This review is the sole final user decision
boundary and is not an execution-time blocker. While the requested outcome remains unfinished, the coordinator
continues to reconcile state and advance the next safe worker or recovery
stage. A completed worker, waiting or incomplete stage,
technical/documentation/review error, Demo/production gate, or quiet interval
is not terminal. The only user pause is a high-risk/material or explicitly
requested plan-review decision; all internal blockers are handled by
rework/replacement or bounded discovery. Record changed evidence and the next
action without treating a progress update as completion.

For a verified transient closure storage or inspection failure, make one
bounded safe retry with the exact returned retry handle and unchanged
idempotency semantics. If it remains unavailable, preserve the completed
outcome and record an honest `closure_unconfirmed` limitation. Do not
manufacture a closure result, silently omit the required review, or describe
completed work as open solely because advisory confirmation is unavailable.

Workers must emit English checkpoints of at most five bullets/150 words and a
final response of at most 300 words. A coordinator wait is at most 60 seconds.
After the first quiet interval, request a checkpoint from the same exact native
task and inspect/list status after later intervals. If it remains running, keep
bounded waiting without publishing an unchanged wait update; elapsed time or
quiet intervals do not prove it is stuck. Interrupt/follow up requires explicit
failed/unavailable/idle-without-work evidence, host-confirmed no-progress, or
user cancellation. Do not expose recovery chatter, skip a planner dependency,
or start downstream work without required report and plan-decision evidence.

When the active tool returns a current contained digest-verified ready plan or
report Markdown projection, copy its server-provided clickable link
byte-for-byte, including its Markdown brackets, label, parentheses, and
destination, with a localized summary explaining why it
matters. Never publish a bare path, raw
task/delegation/report/decision ID, guessed location, stale projection, or
private operational path. Only verified plan/report links are allowed as
user-facing host-private paths; other records remain SQLite-only. If projection
verification fails, omit the link, continue from canonical evidence, and
disclose the human-view limitation only when material.

A plan-review opening stores a durable prompt but does not display that prompt
to the user. After it succeeds, the coordinator must still render the complete
localized decision packet in its final answer. This includes the current
verified link when ready plus the plan's scope, ordered stages, intended
changes, verification, stop conditions, API-key/ENV prerequisites, branch
choices, risks, unresolved items, and approve/revise/cancel choices. Never
compress it to a bare status or question. Workers never raise user decisions;
the coordinator resolves internal blockers autonomously and records any direct
user-authored change as steering.

Use English for coordinator-to-worker and inter-worker messages, report and
decision-normalization content, and other durable coordination prose. Preserve
exact original user text only in its designated decision/task field. Do not
translate a worker report wholesale into progress; summarize only the changed
result, evidence, risk, and next step.
