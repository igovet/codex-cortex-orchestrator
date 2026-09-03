---
name: cortex-control
description: Internal Cortex v1.15.3 task_ref-only semantic companion supplied after explicit cortex:orchestrator activation.
---

# Cortex Control Activation Kernel

This companion applies only after explicit `cortex:orchestrator` selection. The live MCP catalogue is authoritative for every request. Worker text and durable evidence are English; coordinator communication follows the latest meaningful user language.

The `Routing state machine` section in the orchestrator skill is the canonical coordinator router. The worker routing table below is canonical for every worker because this companion, unlike coordinator-only instructions, is reloaded into a worker after compaction.

Coordinator and worker invoke every Cortex operation as its own direct tool call. Cortex operations never run inside programmatic tool calling, `exec`, a batch, or a parallel composition; this preserves the complete live declaration and each individual result as model-visible boundaries. This restriction does not apply to non-Cortex tools.

## Mode boundary

The coordinator owns LLM intent, the dynamic DAG, worker selection, parallelism, model and effort selection, verification/rework/documentation choices, user questions, closure judgment, and final synthesis. The backend owns only durable facts, identity binding, semantic selection, atomicity, replay, and integrity. It never schedules or commands the next workflow step.

A server-issued native child is worker mode, not another coordinator. Every native worker and packaged profile is prohibited from coordinator-only operations, including governance assessment; a planner, replacement, or repeated-planning worker gains no exception. It cannot open tasks, govern, ask users, delegate, dispatch, or close. Its exact first Cortex call is `read_task` using the worker-scoped `task_ref` embedded by the server renderer; that operation has only assignment semantics. Because Desktop supplies no trustworthy initialize identity, pre-identity discovery is a neutral complete catalogue until that exact lifecycle-bound read commits worker role. Worker commitment deliberately does not request a mid-turn catalogue refresh because Desktop can replay the already-successful bootstrap while applying it. An explicit later catalogue read returns only worker operations, while a client retaining the neutral catalogue remains constrained by authoritative server checks. The worker works and publishes only after the read succeeds.

The worker derives a finite minimal first read from the live advertised contract. One materially corrected attempt is permitted only after a deterministic caller-shape rejection whose bounded diagnostics identify an unambiguous local correction. Never repeat the unchanged malformed request, guess identity or authority, or perform project work before consumption succeeds. A second deterministic failure, incomplete diagnostics, or a correction requiring guesses stops the assignment. Ambiguous transport permits only identical reconciliation.

Continue only when the immediately preceding otherwise-identical assignment read reports more data and do so immediately. A terminal assignment read is never repeated. After terminal consumption, proceed to bounded role work and exactly one matching publication; unresolved evidence becomes an honest partial or blocked publication rather than a read or governance loop.

## Worker routing state machine

This table chooses only the next operation kind. The worker derives every complete request from the live advertised schema.

| Observed worker event | Next Cortex route | Do not |
| --- | --- | --- |
| Fresh native worker start | Consume the immutable assignment with `read_task` as the first Cortex operation | Read coordinator state, scope, evidence, continuations, or timeline |
| An assignment page explicitly says more remains | Continue the same assignment read immediately | Change the read or begin project work early |
| The terminal assignment page is consumed | Perform only the bounded assigned work, then call exactly one matching terminal publication operation and stop | Re-read the assignment, delegate, ask the user, or call coordinator operations |
| Work is blocked or needs a user decision | Publish an honest partial or blocked terminal report containing the decision context and stop | Ask the user directly or loop on reads |

After successful native spawn and lifecycle binding, no workflow or governance admission gate may block the worker. Enforce only schema, exact task/actor identity, isolation, immutable relation freshness, atomic replay/conflict, and ledger integrity.

## Anchor boundary

For project work, `open_task` is the first execution operation. Before it, compose only the semantic task contract. Do not inspect the project, dispatch, open a decision, assess governance, or emit task-specific commentary. On success retain only `task_ref`. Ambiguous transport permits only an identical retry. Failure to establish the task ends the Cortex route.

After task creation, make and record one explicit governance-depth decision before the first assignment. Choose the depth from the complete user contract before invoking the assessment operation. Fully specified, bounded, reversible low-risk work with no remaining product choice is minimal even when mechanically multi-step; meaningful choices or unresolved cross-surface branches are light; security, privacy, credentials, money, destructive, or production-critical work is full. Only the root coordinator owns this decision and any later reassessment. The public assignment boundary rejects a missing assessment. A complete risk-free minimal plan is informational; light/full governance, uncertainty, incomplete evidence, unresolved items, or plan-discovered risk requires exact plan approval regardless of language. A planner cannot self-attest a downgrade. Accepted semantic steering atomically revokes every nonterminal worker bound to an earlier contract revision and requires a fresh current-contract assignment.

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

Native wait output is advisory host coordination. A timeout or empty wait while the child remains active does not justify a task-state read; wait again without polling the ledger. Read current state or relevant evidence after reported completion or attention, a visible child-completion notification, user steering, or recovery/compaction. A finalized worker publication is authoritative durable completion evidence and is consumed without another wait for that child even when host completion output is contradictory. Within the active coordinator turn, route that evidence to the next current-contract assignment, verification, recovery, rework, review, or result action without asking the user to re-authorize bounded work. Lifecycle stop without publication uses explicit loss/recovery. `read_state` is never a worker-liveness poll, and host wait output never suppresses durable evidence.

A host-confirmed terminal worker stop without publication, including a terminal worker connection error, is concrete loss evidence. The coordinator reads the current responsibility scope and creates one lineage-linked replacement immediately. It never repeats the original assignment opening, respawns a replayed dispatch, asks the user to say “continue”, or leaves the task idle when that recovery route is available.

After coordinator recovery or compaction, use one scalar state read to choose the route. If delegated work is active or unfinished, read active continuations next; never use the historical timeline as a continuation lookup. Timeline reads are reserved for an explicit chronology or audit need.

A question known in advance to select previously unstated product behavior opens steering before it is presented. The user's direct answer is the steering answer; do not route it through ordinary clarification. When the user already states a concrete semantic change, record that exact message directly as steering and never open a duplicate confirmation. The same applies when a factual clarification answer itself contains the change. Ordinary clarification is only for facts whose possible answers leave every current outcome detail unchanged. Do not open either question type for an already-approved bounded rerun, routine independent verification, report rework, recovery, or another action wholly inside the current acceptance, constraints, and verification.

All later calls follow the orchestrator skill and live schemas. Neither coordinator nor worker copies any other identifier, handle, digest, cursor, revision, slot, or idempotency value. Server-owned read continuation is requested only by its boolean flag. After `open_assignment` succeeds, native spawn follows immediately with the exact returned dispatch and no intervening model narration, read, or tool call.
