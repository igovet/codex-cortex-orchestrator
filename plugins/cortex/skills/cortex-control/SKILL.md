---
name: cortex-control
description: Internal Cortex v1.14.0 task_ref-only semantic companion supplied after explicit cortex:orchestrator activation.
---

# Cortex Control Activation Kernel

This companion applies only after explicit `cortex:orchestrator` selection. The live MCP catalogue is authoritative for every request. Worker text and durable evidence are English; coordinator communication follows the latest meaningful user language.

## Mode boundary

The coordinator owns LLM intent, the dynamic DAG, worker selection, parallelism, model and effort selection, verification/rework/documentation choices, user questions, closure judgment, and final synthesis. The backend owns only durable facts, identity binding, semantic selection, atomicity, replay, and integrity. It never schedules or commands the next workflow step.

A server-issued native child is worker mode, not another coordinator. It cannot open tasks, govern, ask users, delegate, dispatch, or close. Its exact first Cortex call is the assignment view of `read_task` using the worker-scoped `task_ref` embedded by the server renderer. It works and publishes only after that lifecycle-bound read succeeds.

After successful native spawn and lifecycle binding, no workflow or governance admission gate may block the worker. Enforce only schema, exact task/actor identity, isolation, immutable relation freshness, atomic replay/conflict, and ledger integrity.

## Anchor boundary

For project work, `open_task` is the first execution operation. Before it, compose only the semantic task contract. Do not inspect the project, dispatch, open a decision, assess governance, or emit task-specific commentary. On success retain only `task_ref`. Ambiguous transport permits only an identical retry. Failure to establish the task ends the Cortex route.

After task creation, make and record one explicit governance-depth decision before the first assignment. Choose the depth from the current evidence before invoking the assessment operation; a rationale or risk notes without a selected depth do not satisfy this requirement. The public assignment boundary rejects a missing assessment. It also rejects light/full delivery until the current finalized plan declares required review and an explicit approval is bound to that exact plan identity and digest. Planning and evidence assignments remain available to establish this relation, and no such pre-dispatch check blocks an already bound worker.

Closure is a user decision boundary, not an automatic finalization step. Before
every `close_task` attempt, the coordinator must present the reconciled,
verified result and its impact in the latest meaningful user language, then
open one closure-review hold with exactly two choices: revise the current task
or close the task. The coordinator must wait for and record the explicit answer;
silence, prior approval, worker completion, or a ready-looking ledger state is
not permission to close. A revise answer keeps the same task reference alive
and routes bounded rework/replacement from the current evidence. A close answer
is the only answer that permits closure. Any new result or rework invalidates
an earlier closure review and requires a fresh one.

All later calls follow the orchestrator skill and live schemas. Neither coordinator nor worker copies any other identifier, handle, digest, cursor, revision, slot, or idempotency value. Server-owned read continuation is requested only by its boolean flag. After `open_assignment` succeeds, native spawn follows immediately with the exact returned dispatch and no intervening model narration, read, or tool call.
