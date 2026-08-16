---
name: context-compaction
description: Produce concise, evidence-based handoffs between Codex agents or work phases. Use when a task crosses agents, resumes after long investigation, or has noisy logs and reports that would otherwise pollute the parent context.
---

# Context Handoff

Do not pass raw transcripts by default. Create a compact handoff containing only:

1. Goal and acceptance criteria.
2. Verified repository facts with file and symbol references.
3. Decisions made and alternatives rejected.
4. Changed files or current diff state.
5. Commands run and decisive outputs.
6. Open questions, blockers, and next action.

Use short summaries for older logs and reports. Preserve exact error output only when the next agent needs it to reproduce or diagnose the issue. Never summarize secrets into the handoff. The parent thread should integrate the handoff; do not depend on private host databases or resume a failed subagent session.

## Cortex recovery after context reset or compaction

When the coordinator resumes after automatic/manual compaction or a host
`clear` context reset,
do not assume that the active skill version, transient protocol reminders, or
the last visible lifecycle response survived. Preserve the opaque `task_ref`
and call `manage_orchestration` with `intent="inspect"` exactly once for that
task. Treat the returned `context_handoff`, current pipeline, report refs, and
relative step as the authoritative recovery snapshot. Reconcile
`pending_dispatches` against the top-level inspect `dispatches` and invoke only
those exact still-unstarted requests. Treat `active_workers[].host_agent_id` as
the exact persisted native wait targets; never respawn them. A host wait-any
form may omit explicit targets only while one of those bound workers is
running. Treat `stopped_workers` as non-waitable: consume their persisted
report refs, surface durable question refs, or submit their exact failed result
to `continue_orchestration` so Cortex applies rework. Never use
`followup_task` to repair a stopped worker's report error; it is permitted only
for the same question-paused worker after the durable answer is recorded. Each pending dispatch retains its `dispatch_ref`, immutable
`briefing_path`, and `briefing_digest`, but the coordinator must not read or
inline the briefing. Do not call
`start_orchestration` again, replay completed dispatches, or reconstruct state
from a raw transcript. After rehydration, continue the existing task and
publish every exact `report_markdown_link` before the next lifecycle or report
read call.
