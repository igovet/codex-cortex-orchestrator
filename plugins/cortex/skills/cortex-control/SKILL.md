---
name: cortex-control
description: Internal Cortex v1.14.14 task_ref-only semantic companion supplied after explicit cortex:orchestrator activation.
---

# Cortex Control Activation Kernel

This companion applies only after explicit `cortex:orchestrator` selection. The live MCP catalogue is authoritative for every request. Worker text and durable evidence are English; coordinator communication follows the latest meaningful user language.

Coordinator and worker invoke every Cortex operation as its own direct tool call. Cortex operations never run inside programmatic tool calling, `exec`, a batch, or a parallel composition; this preserves the complete live declaration and each individual result as model-visible boundaries. This restriction does not apply to non-Cortex tools.

## Mode boundary

The coordinator owns LLM intent, the dynamic DAG, worker selection, parallelism, model and effort selection, verification/rework/documentation choices, user questions, closure judgment, and final synthesis. The backend owns only durable facts, identity binding, semantic selection, atomicity, replay, and integrity. It never schedules or commands the next workflow step.

A server-issued native child is worker mode, not another coordinator. Every native worker and packaged profile is prohibited from coordinator-only operations, including governance assessment; a planner, replacement, or repeated-planning worker gains no exception. It cannot open tasks, govern, ask users, delegate, dispatch, or close. Its exact first Cortex call is the assignment view of `read_task` using the worker-scoped `task_ref` embedded by the server renderer. Because Desktop supplies no trustworthy initialize identity, pre-identity discovery is a neutral complete catalogue until that exact lifecycle-bound read commits worker role. The server then requests a catalogue refresh; supporting clients narrow to worker read/publication operations, while clients that retain the initial catalogue remain constrained by authoritative server checks. The worker works and publishes only after the read succeeds.

The worker derives a finite minimal first read from the live advertised contract. One materially corrected attempt is permitted only after a deterministic caller-shape rejection whose bounded diagnostics identify an unambiguous local correction. Never repeat the unchanged malformed request, guess identity or authority, or perform project work before consumption succeeds. A second deterministic failure, incomplete diagnostics, or a correction requiring guesses stops the assignment. Ambiguous transport permits only identical reconciliation.

Continue only when the immediately preceding otherwise-identical assignment read reports more data and do so immediately. A terminal assignment read is never repeated. After terminal consumption, proceed to bounded role work and exactly one matching publication; unresolved evidence becomes an honest partial or blocked publication rather than a read or governance loop.

After successful native spawn and lifecycle binding, no workflow or governance admission gate may block the worker. Enforce only schema, exact task/actor identity, isolation, immutable relation freshness, atomic replay/conflict, and ledger integrity.

## Anchor boundary

For project work, `open_task` is the first execution operation. Before it, compose only the semantic task contract. Do not inspect the project, dispatch, open a decision, assess governance, or emit task-specific commentary. On success retain only `task_ref`. Ambiguous transport permits only an identical retry. Failure to establish the task ends the Cortex route.

After task creation, make and record one explicit governance-depth decision before the first assignment. Choose the depth from the current evidence before invoking the assessment operation; a rationale or risk notes without a selected depth do not satisfy this requirement. Only the root coordinator owns this decision and any later reassessment, which requires material risk-changing evidence and another deliberate current-depth choice. Worker completion, repeated planning, and plan revision do not themselves trigger reassessment. The public assignment boundary rejects a missing assessment. It also rejects light/full delivery until the current finalized plan declares required review and an explicit approval is bound to that exact plan identity and digest. Planning and evidence assignments remain available to establish this relation, and no such pre-dispatch check blocks an already bound worker.

Closure is a user decision boundary, not an automatic finalization step. Before
every `close_task` attempt, the coordinator must present the reconciled,
verified result and its impact in the latest meaningful user language, then
use the advertised `open_clarification` operation to open one closure-review
hold with exactly two choices: revise the current task or close the task. The
coordinator must wait for the explicit answer and record it through the
advertised `record_clarification` operation;
silence, prior approval, worker completion, or a ready-looking ledger state is
not permission to close. A request made before the result existed to close
automatically afterward is not the required current review. Never probe the
closure operation before the review has been opened and recorded. A revise
answer keeps the same task reference alive
and routes bounded rework/replacement from the current evidence. A close answer
is the only answer that permits closure. Any new result or rework invalidates
an earlier closure review and requires a fresh one.

Native wait output is advisory host coordination. After every bounded native wait returns—including timeout, an empty result, or a contradiction with visible child completion—the coordinator immediately reads current task state or relevant evidence before it may wait again. A finalized worker publication is authoritative durable completion evidence and is consumed without another wait for that child. With no publication, an active child may be waited on again; lifecycle stop without publication uses explicit loss/recovery. Empty host output never suppresses durable evidence, and the coordinator never remains in model-only waiting after the wait call returns.

All later calls follow the orchestrator skill and live schemas. Neither coordinator nor worker copies any other identifier, handle, digest, cursor, revision, slot, or idempotency value. Server-owned read continuation is requested only by its boolean flag. After `open_assignment` succeeds, native spawn follows immediately with the exact returned dispatch and no intervening model narration, read, or tool call.
