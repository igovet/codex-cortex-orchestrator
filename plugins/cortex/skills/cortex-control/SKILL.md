---
name: cortex-control
description: Internal Cortex v1.13.1 task_ref-only semantic companion supplied after explicit cortex:orchestrator activation.
---

# Cortex Control Activation Kernel

This companion applies only after explicit `cortex:orchestrator` selection. The live MCP catalogue is authoritative for every request. Worker text and durable evidence are English; coordinator communication follows the latest meaningful user language.

## Mode boundary

The coordinator owns LLM intent, the dynamic DAG, worker selection, parallelism, model and effort selection, verification/rework/documentation choices, user questions, closure judgment, and final synthesis. The backend owns only durable facts, identity binding, semantic selection, atomicity, replay, and integrity. It never schedules or commands the next workflow step.

A server-issued native child is worker mode, not another coordinator. It cannot open tasks, govern, ask users, delegate, dispatch, or close. Its exact first Cortex call is the assignment view of `read_task` using the worker-scoped `task_ref` embedded by the server renderer. It works and publishes only after that lifecycle-bound read succeeds.

After successful native spawn and lifecycle binding, no workflow or governance admission gate may block the worker. Enforce only schema, exact task/actor identity, isolation, immutable relation freshness, atomic replay/conflict, and ledger integrity.

## Anchor boundary

For project work, `open_task` is the first execution operation. Before it, compose only the semantic task contract. Do not inspect the project, dispatch, open a decision, assess governance, or emit task-specific commentary. On success retain only `task_ref`. Ambiguous transport permits only an identical retry. Failure to establish the task ends the Cortex route.

After task creation, record one governance assessment before the first assignment. The public assignment boundary rejects a missing assessment. It also rejects light/full delivery until the current finalized plan declares required review and an explicit approval is bound to that exact plan identity and digest. Planning and evidence assignments remain available to establish this relation, and no such pre-dispatch check blocks an already bound worker.

All later calls follow the orchestrator skill and live schemas. Neither coordinator nor worker copies any other identifier, handle, digest, cursor, revision, slot, or idempotency value. Server-owned read continuation is requested only by its boolean flag. After `open_assignment` succeeds, native spawn follows immediately with the exact returned dispatch and no intervening model narration, read, or tool call.
