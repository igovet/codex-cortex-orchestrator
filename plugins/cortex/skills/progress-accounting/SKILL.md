---
name: progress-accounting
description: Keep Codex task progress reporting concise and evidence-focused without collecting unnecessary token, model, or private telemetry.
---

# Minimal Progress Accounting

Track only task-relevant evidence: outcome, scope, delegations used, reports,
checks run, pass/fail/blocked/unverified status, material risk, and verified
human-view artifacts. Do not record token counts, hidden reasoning,
secret-bearing prompts, private native handles, raw diagnostics, or raw
conversation transcripts in repository files or ledger prose.

Retain the canonical project root, `task_id` when one exists, the current
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
action. Use the language of the latest meaningful user message. Lead with what
changed, state the evidence-backed implication, and end with the next step.
Suppress unchanged waits, repeated summaries, routine pagination, chunk
assembly, retry attempts, and internal worker-recovery chatter.

Workers must emit English checkpoints of at most five bullets/150 words and a
final response of at most 300 words. A coordinator wait is at most 60 seconds.
After the first quiet interval, request a checkpoint from the same exact native
task and inspect/list status after later intervals. If it remains running, keep
bounded waiting and update the user; elapsed time or quiet intervals do not
prove it is stuck. Interrupt/follow up requires explicit
failed/unavailable/idle-without-work evidence, host-confirmed no-progress, or
user cancellation. Do not expose recovery chatter, skip a planner dependency,
or start downstream work without required report and plan-decision evidence.

When the active tool returns a current contained digest-verified ready plan or
report Markdown projection, copy its server-provided `markdown_link` field
byte-for-byte as the clickable link, with a localized summary explaining why it
matters. Never publish a bare path, raw
task/delegation/report/decision ID, guessed location, stale projection, or
private operational path. Only verified plan/report links are allowed as
user-facing host-private paths; other records remain SQLite-only. If projection
verification fails, omit the link, continue from canonical evidence, and
disclose the human-view limitation only when material.

Use English for coordinator-to-worker and inter-worker messages, report and
decision-normalization content, and other durable coordination prose. Preserve
exact original user text only in its designated decision/task field. Do not
translate a worker report wholesale into progress; summarize only the changed
result, evidence, risk, and next step.
