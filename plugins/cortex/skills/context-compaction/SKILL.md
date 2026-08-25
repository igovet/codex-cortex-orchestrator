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

For an active coordinator task, the private handoff must preserve the exact
pair returned by start:

```text
task_ref + coordinator_ref
```

Keep the pair together only in the coordinator's bounded private handoff. Do
not write the raw capability to durable task state, a result, worker bootstrap,
event, diagnostic, path, artifact, tool argument, or user-facing text. Also
retain only the exact native child IDs already returned and bound by the
runtime, canonical result refs, and the most recent server-derived step.

If both values survive, a coordinator may call
`manage_orchestration({task_ref, coordinator_ref, intent:"inspect"})` once
when it needs the current lifecycle state. Follow only returned exact native
dispatches, exact eligible live waits, durable question paths, canonical result
reads, continuations, or terminal outcomes. Do not replay a completed dispatch,
infer an identity, or use a generic collaboration tool.

If either `task_ref` or `coordinator_ref` is absent, fail closed. Do not
inspect, recover, query a ledger, bind a session, scan an environment, infer a
task from an active worker, construct a result, synthesize a replacement, or
start a task merely because an older task is visible. Obtain fresh user
direction and fresh route activation before starting a new task.

Workers do not preserve a coordinator capability. They resume only when their
native bootstrap still provides the exact `task_ref + assignment_ref` pair.
Each worker operation preserves that pair; predecessor reads additionally use
the explicitly granted `attempt_result_ref`. Loss or mismatch fails closed and
is reported as a neutral limitation to the coordinator. Hooks are telemetry
only and never rehydrate assignment authority, briefing paths, ledger paths, or
bearers.

Before any Cortex/project call, an initial native child missing either worker
ref returns only `CORTEX_WORKER_BOOTSTRAP_MISSING missing_fields=[...] retryable=true`
and makes zero calls, including no `worker_question`. A
coordinator that retained the exact original server dispatch may use one
`followup_task` on that same child by byte-copying its server-built
`bootstrap_repair_message` unchanged. It never reconstructs a pair or message
from compaction, session, environment, thread, path, database, or hook state
and never spawns a replacement. A second missing/invalid result—or loss of the
server-built message—is terminal and fail-closed.
