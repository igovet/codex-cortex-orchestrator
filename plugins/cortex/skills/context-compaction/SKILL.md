---
name: context-compaction
description: Internal Cortex recovery overlay. Load only for an explicitly activated Cortex task after compaction, reset, or a required bounded handoff; never select for ordinary work.
---

# Context Handoff

Do not pass raw transcripts by default. Create a compact handoff containing only:

1. Goal and acceptance criteria.
2. Verified repository facts with file and symbol references.
3. Decisions made and alternatives rejected.
4. Changed files or current diff state.
5. Commands run and decisive outputs.
6. Open questions, blockers, and next action.

Use short summaries for older logs and results. Preserve exact error output only when the next agent needs it to reproduce or diagnose the issue. Never summarize secrets into the handoff. The parent thread should integrate the handoff; do not depend on private host databases or resume a failed subagent session.

Compactness is a prompt-writing preference, not a data limit. Keep a handoff
concise when the information remains complete, but never truncate, omit, or
reject material task, plan, result, event, question, answer, or artifact data
to meet a byte, character, or file-size target. When the complete content is
large, include it intact or use the exact authorized artifact/reference that
contains it; the backend stores the complete submitted content.

## Cortex recovery after context reset or compaction

When the coordinator resumes after automatic/manual compaction or a host
`clear` context reset,
do not assume that the active skill version, transient protocol reminders, or
the last visible lifecycle response survived. Preserve the opaque `task_ref`
and call `manage_orchestration` with `intent="inspect"` exactly once for that
task. Treat the returned `context_handoff`, current pipeline, result refs, and
relative step as the authoritative recovery snapshot. Reconcile
`pending_dispatches` against the top-level inspect `dispatches` and invoke only
those exact still-unstarted requests. Treat `active_workers[].host_agent_id` as
the exact persisted native wait targets; never respawn them. A host wait-any
form may omit explicit targets only while one of those bound workers is
running. If an exact targeted wait receives a host identity-unavailable proof,
the `PostToolUse` hook records only that child as a terminal resultless recovery
state; inspect it once, then invoke the server-owned `recover_inspect` action.
A timeout, transport
failure, generic error, or ambiguous multi-target error does not prove a child
ended and never authorizes a replacement. Treat `stopped_workers` as non-waitable: consume their persisted
result refs, surface durable question refs, or invoke the exact server-returned
recovery action. `recover_inspect` derives the stopped attempt, dispatch, and
rework scope from the ledger; the coordinator must not synthesize, submit, or
replay a failed result and must not construct a replacement payload. Never use
`followup_task` to repair a stopped worker's result error; it is permitted only
for the same question-paused worker after the durable answer is recorded. Each pending dispatch retains its `dispatch_ref`, immutable
`briefing_path`, and `briefing_digest`, but the coordinator must not read or
inline the briefing. Do not call
`start_orchestration` again, replay completed dispatches, or reconstruct state
from a raw transcript. After rehydration, continue the existing task. Consume
only the exact server-returned AttemptResult refs and completion summary; do
not create, publish, or republish a separate human projection artifact during
recovery.

When the resumed session is a worker (`SubagentStart`/worker `SessionStart`),
the lifecycle hook rehydrates only the exact attempt-bound immutable briefing
(assignment), compiled plan unit, and user-intent artifact, each with its
SHA-256 digest. The worker must verify those artifacts and continue the same
attempt; ambiguous identity or missing artifacts is internal recovery evidence
that Cortex routes to a diagnostic worker. It must not
reconstruct assignment, plan, intent, or result contract from the transcript,
or read the shared ledger.
