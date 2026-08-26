---
name: context-compaction
description: Internal Cortex v11 recovery overlay. Load only for an explicitly activated Cortex task after compaction, reset, or a required bounded handoff; never select for ordinary work.
---

# Context Handoff

Do not pass raw transcripts by default. A bounded private handoff contains
only the current goal and acceptance criteria, verified repository facts and
source references, decisions, changed files, commands and decisive outputs,
open task questions, and the next server-derived action. Never summarize
secrets, credentials, raw worker reports, private logs, or raw tool streams.

Compactness is prompt guidance, not a data limit. Preserve material task,
result, question, answer, and artifact content through the authorized public
reference when it cannot fit safely in a concise handoff.

## Cortex v11 compaction rule

After a coordinator compaction, clear, or reset in the same authenticated host
incarnation, call `inspect_orchestration` before any wait, replay, follow-up,
continuation, resume, or child creation. Consume every returned page in order
until `complete=true`; no earlier page authorizes an action. If another context
boundary invalidates the cursor, restart that inspection from its first page.

For an active coordinator task, the private handoff must preserve the exact
opaque coordinator authority returned when the task starts. Keep that authority
together only in the coordinator's bounded private handoff. Do
not write the raw capability to durable task state, a result, worker bootstrap,
event, diagnostic, path, artifact, tool argument, or user-facing text. Also
retain only already-bound native workers, canonical result references, and the
most recent server-derived step without exposing host identity.

If the authority survives, a coordinator may use the current public lifecycle
operation to obtain the server-derived state. Follow only returned native
dispatches, generic timeout-bounded waits, durable question paths, canonical
result reads, continuations, or terminal outcomes. Do not replay a completed
dispatch, infer an identity, or use any collaboration operation outside the
native lifecycle.

If coordinator authority is absent, fail closed. Do not
inspect, recover, query a ledger, bind a session, scan an environment, infer a
task from an active worker, construct a result, synthesize a replacement, or
start a task merely because an older task is visible. Obtain fresh user
direction and fresh route activation before starting a new task.

Workers do not preserve coordinator authority. Their initial native dispatch
and complete briefing read establish one exact child authority. Only that
already-established child may later use the no-identity context refresh after
its own compaction; refresh never discovers a task, scans siblings, or mints
authority. Predecessor access remains limited by the active schema. Loss or
mismatch fails closed and is reported as a neutral limitation to the
coordinator. Model-visible hook output is telemetry only; the
trusted local lifecycle observer suite never rehydrates worker authority,
briefing paths, ledger paths, or bearers and is not a model-visible recovery
surface.

Before any Cortex/project call, an initial native child whose first operation
reports pending trusted spawn observation retries only that operation with
bounded backoff until a finite deadline and makes zero project calls. It never
switches operations or spawns a replacement. An exact successful retry
automatically clears the transient observer failure. At the deadline, or when dispatch
authority is otherwise absent, it follows only public fail-closed recovery. The
coordinator may use `followup_task` only when the server explicitly permits
same-worker recovery. It never reconstructs an authority value from compaction,
session, environment, thread, path, database, or hook state. A rejected or
missing recovery route is terminal and fail-closed.
