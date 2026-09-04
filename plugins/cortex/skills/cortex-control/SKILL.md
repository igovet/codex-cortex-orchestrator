---
name: cortex-control
description: Internal Cortex v1.15.6 task_ref-only semantic companion supplied after explicit cortex:orchestrator activation.
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
| The terminal assignment page is consumed | Perform only the bounded assigned work, then call exactly one matching terminal publication operation and stop: plan only for planning, documentation only for an explicitly documentation-impact assignment, otherwise result | Treat read-only evidence as documentation merely because no files changed; re-read the assignment, delegate, ask the user, or call coordinator operations |
| Work is blocked or needs a user decision | Publish an honest partial or blocked terminal report containing the decision context and stop | Ask the user directly or loop on reads |

After lifecycle binding, governance does not retroactively change the immutable assignment. Identity, exact node coverage, revision and artifact freshness, mutation boundaries, publication uniqueness and ledger integrity remain enforceable. A non-publication result ends that worker route without retry or further project work; the coordinator interprets current evidence and chooses bounded reconciliation.

## Anchor boundary

For project work, `open_task` is the first execution operation. Before it, compose only the semantic task contract. Do not inspect the project, dispatch, open a decision, assess governance, or emit task-specific commentary. On success retain only `task_ref`. Ambiguous transport permits only an identical retry. Failure to establish the task ends the Cortex route.

After task creation, make and record one explicit governance-depth decision before the first assignment. Choose the depth from the complete user contract before invoking the assessment operation. Fully specified, bounded, reversible low-risk work with no remaining product choice is minimal even when mechanically multi-step; ordinary multi-step or cross-surface work is light; security, privacy, credentials, money, destructive, or production-critical work is full. Only the root coordinator owns this decision and any later reassessment. The public assignment boundary rejects a missing assessment. A complete risk-free minimal or ordinary light plan is informational; materially high-risk/full work, a genuine product or authority choice, a credential prerequisite, or explicit user-requested review requires its decision packet. Incomplete work and uncertainty alone require bounded discovery, not automatic user approval. A planner cannot self-attest a downgrade. Accepted semantic steering atomically revokes every nonterminal worker bound to an earlier contract revision and requires a fresh current-contract assignment.

A committed semantic change revokes old-revision authority. Interrupt the affected protected native tasks, obtain current host lifecycle evidence, and reconcile the artifact before new execution. Prepare and independently validate the revised candidate. A revision alone does not require another review; follow the current material-risk, authority, credential and explicit-review policy.

Closure is a user decision boundary after evidence reconciliation. The
coordinator presents the reconciled result, opens the mandatory closure-review
question with the localized revise/close choices, waits for the direct response,
and records it before calling `close_task`. Only a current recorded `close`
choice authorizes closure; `revise` keeps the same task reference alive for
bounded rework and requires a fresh review after the new evidence. Any partial
or blocked result is routed to autonomous rework before asking for the final
choice, not hidden by an automatic close.

Native wait output is advisory host coordination. A timeout or empty wait while the child remains active does not justify a task-state read; wait again without polling the ledger. Read current state or relevant evidence after reported completion or attention, a visible child-completion notification, user steering, or recovery/compaction. A finalized worker publication is authoritative durable completion evidence and is consumed without another wait for that child even when host completion output is contradictory. Within the active coordinator turn, route that evidence to the next current-contract assignment, verification, recovery, rework, review, or result action without asking the user to re-authorize bounded work. Lifecycle stop without publication uses explicit loss/recovery. `read_state` is never a worker-liveness poll, and host wait output never suppresses durable evidence.

A terminal worker stop without publication is a recovery event, not permission to guess a replacement. Obtain the complete unfiltered native-agent projection and use its signed observation with the current responsibility scope. The backend checks the exact protected owner and returns the bounded reconciliation/replacement route. The coordinator never repeats the original assignment opening, spawns a replayed dispatch, or asks the user to say “continue”. Timeout and silence do not prove loss.

After coordinator recovery or compaction, use one scalar state read to choose
the route. If delegated work is active or unfinished, `read_continuations` is
the immediate next Cortex operation, before queued user steering or any scope,
outcome, evidence, plan, assignment, or timeline operation. Consume that
continuation view first, then record any queued direct semantic change
immediately so stale nonterminal ownership is revoked rather than awaited.
Never use the historical timeline as a continuation lookup. Timeline reads are
reserved for an explicit chronology or audit need.

A real product or authority choice belongs in an independently validated plan-review packet when its alternatives can be responsibly planned. If the missing answer prevents constructing valid alternatives, open one genuine pre-plan steering decision. A direct user change is already authorized: record it once, and do not open either question type to repeat or confirm it. Internal failures, retries, prerequisites and in-contract rework do not require user decisions. Closure always requires its own fresh post-result review.

If a direct semantic change arrives while a native assignment is nonterminal, record it immediately after the required fresh reads; never wait for old publication. After coordinator recovery, consume the required continuation view first and then apply queued changes in order. After commit, interrupt affected protected tasks and confirm quiescence before artifact reconciliation. A late still-bound publication is non-current evidence, not permission to overwrite the report or retry against a guessed revision.

An approved current plan covers its unchanged scope, not arbitrary new authority. Failed, partial and unrun checks feed finite diagnostic, repair and independent-regression routes. Use exact ready node selections; never reopen a completed assignment or overwrite failed evidence. Honor bounded expansion and non-progress exhaustion. Continue available authorized work without another question; if no safe route remains, present unresolved evidence honestly rather than fabricate success or loop forever.

All later calls follow the orchestrator skill and live schemas. Neither coordinator nor worker copies any other identifier, handle, digest, cursor, revision, slot, or idempotency value. Server-owned read continuation is requested only by its boolean flag. After `open_assignment` succeeds, native spawn follows immediately with the exact returned dispatch and no intervening model narration, read, or tool call.
