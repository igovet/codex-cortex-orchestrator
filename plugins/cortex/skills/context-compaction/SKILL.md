---
name: context-compaction
description: Restore coordinator and specialist context after compaction or restart. For the initial skill read, emit the complete command result with text(result), preserving output and exit_code or a running session_id.
---

# Context recovery

Use this skill after summarization, compaction, reset, restart or terminal worker
recovery. The recovery summary is an index into durable state, not a replacement for
the original request, pipeline or evidence.
Reload the next needed live tool declaration; remembered field names and values
are not authoritative. Prefer its declared defaults.

## Coordinator

Resume the same native thread and task. Recover the current pipeline and reread the
original request, later user messages, attachment routes and as many bounded evidence
pages as needed to restore exact requirements and make the next decision. Restore the user-facing language from the user's own prose and any explicit
response-language preference before replying; an English recovery summary does not
change it.

Reconstruct active and cancelled conditions, decisions, assignments, resource owners,
open actions, required checks, worker handles, source revisions, artifact revisions
and report pointers. Reconcile active workers before overlapping work. Apply queued
steering and decide whether change signals invalidate prior evidence.
Restore ownership before project work: original user sources, attachments and bounded
evidence pages may be reread by the coordinator, while project reads and checks go to
the worker that owns them. Repeat a project check for missing or contradictory
evidence, changed requirements or source/artifact revisions, or changed worker state.

## Worker

Resume English-only reasoning and communication immediately; the coordinator's
user-facing language does not apply to workers.
Restore the assigned worker skill, exact assignment, model and effort, source
revision, owned resources, artifact state, command receipts, report pointers and any
unpublished draft. Reread the assignment, clarifications and evidence required for
correctness. Inspect current state before mutation and preserve the same ownership
when continuation is possible.

Use `list_reports` only when a required saved reference was lost and cannot be
recovered from the pipeline or assignment. Follow bounded cursors for needed content.
Do not create a duplicate task or infer new scope from unrelated reports. If a source
or attachment cannot be reopened, record that gap explicitly and ask the coordinator
for the missing input.
