---
name: context-compaction
description: Restore coordinator and specialist context before continuing after summarization, compaction or restart.
---

# Context recovery

## When this applies

After summarization, compaction, reset or process restart, reread durable context
before any task-specific answer (including a recap), project work, delegation,
pipeline updates or processing queued steering. Completion before compaction does
not exempt a later task-related reply from rereading.
A summary is an orientation aid, not a replacement for rereading saved materials.

## Preserve in host context

- Explicitly selected Cortex route, which remains active for follow-ups even if
  the previous change completed; do not replace it with ordinary direct execution.
- The same native thread and parent lineage; relevant report references.
- Direct assignments, exact requirements, constraints and acceptance checks.
- Selected worker skill identities, models and reasoning effort.
- Native worker handles, current assignment and predecessor report, known findings
  and remaining work; selected decision briefs, unresolved alternatives and checks.

Do not retain secrets or raw logs. Restore complete named skills from retained host
attachments or their exact advertised SKILL.md paths. Read only needed declared
Markdown references; never inspect agent TOML, server internals or the installation
tree. A catalogue entry alone is not the skill body. Missing automatic injection
does not prevent standard skill-file loading.

## Coordinator recovery

1. Restore the active route in the same native thread; the host resolves its task.
2. Obtain fresh newest-first catalogue previews and read the current pipeline beginning.
3. Restore requirements, constraints, decisions, ownership, models/effort and native handles.
4. Reconcile native worker status before overlapping work; silence is not failure.
   Preserve the distinction between completed assignment, interrupted turn and
   released slot. Do not interrupt pending_init contexts to free capacity or repeat
   rejected spawns without an observed capacity change.
5. Apply queued changes, update the pipeline and resume delegation or native wait.

Do not read original-request bodies, project indexes or source. Restore a selected
result report's opening decision brief only when a consequential decision needs it;
never continue into its detailed evidence. Normal skill loading does not authorize
project-file reading. If a detail remains missing, delegate its recovery.

## Worker recovery

1. Restore the assigned specialist worker skill and reload the required named skills
   through Codex. Report missing worker skill availability to the coordinator;
   do not reconstruct it through installation inspection.
2. Restore the directly assigned work, mandatory conditions, model and effort.
3. Obtain a fresh catalogue and reread the current pipeline beginning.
4. Read relevant reports, including prior own results when useful, and routed docs.
5. Resume within the assignment; ask the coordinator for missing obligations.

## Limits

- Resume the same native thread and automatically resolved pipeline; do not create a duplicate after context loss.
- Follow cursors only for needed text. If a pipeline cursor is stale, restart at its beginning.
- Never read all reports automatically or infer new scope from an unrelated report.
- If host task context cannot be recovered, report that concrete limitation. Ask an
  unregistered parent to access its task first; never request or guess a task identifier.
