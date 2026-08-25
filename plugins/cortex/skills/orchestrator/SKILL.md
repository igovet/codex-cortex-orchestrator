---
name: orchestrator
description: Explicit opt-in Cortex v11 coordinator. Use only when the user directly selects or mentions cortex:orchestrator. Never activate from task complexity alone.
---

# Cortex Orchestrator v11

## Invocation

In Codex Desktop, select `cortex:orchestrator` in Skills or mention
`$cortex:orchestrator`. In CLI, use `$cortex:orchestrator` or `/skills`.
`/cortex` is not a native slash command. The host, not task data, establishes
the activation context for the first lifecycle call.

Select `empty` to start an explicitly activated task, `help` for lifecycle-free
guidance, `harvest` or `harvest-refresh` for the respective explicit knowledge
routes, or `normal` to leave the route. For another value, return the
schema-defined route diagnostic and recovery; do not ask the user to resolve
protocol recovery.

For every activated orchestration route, read and follow
`../cortex-control/SKILL.md` before the first lifecycle call. That bundled file
and the current public MCP schema registry are the runtime contract. Project-
local instructions are not part of the installed Cortex contract.

## Coordinator boundary

The root coordinator is never a project worker. It must not inspect, search,
read, edit, patch, build, test, or run the target project, plugin cache, or
private runtime state itself. It may clarify user intent, call public Cortex
tools, invoke exact returned native dispatches, wait, read canonical results,
route durable user questions, and deliver a verified terminal outcome. Every
project operation belongs to a worker.

The root does not create visible threads, run another execution layer, use
generic collaboration calls, or substitute a local implementation for a missing
or failed worker. Native dispatch, exact wait, and same-child follow-up are the
only worker lifecycle primitives.

## Capabilities and governance selection

Cortex issues private coordinator authority and separate exact opaque authority
for each native worker. Keep them private, preserve them byte-for-byte only
through tool-authorized calls, and never infer them from
task history, a path, a child, a session, a hook, the environment, or a ledger
record. A missing capability fails closed; only a fresh explicit activation can
create a fresh task.

The model authors waves within the active schema. The service validates and
returns dispatches; it does not reconstruct a model-owned pipeline. `auto` is
the normal governance selection. Use `required` only if the user explicitly
asks for full governance and `off` only if they explicitly opt out. The service
clamps either selection when needed. Do not make an unstated hidden-risk
assessment on the user's behalf.

When a worker reports a dispatch-authority failure, follow only the exact
server-returned same-child recovery. Do not reconstruct authority or spawn a
replacement. A later failure follows returned recovery; terminal child prose
does not authorize a coordinator operation.

## Normal lifecycle

1. Before starting, preserve the user's material request and attachments through
   the host's supported input surface. Ask one task-relevant question only when
   material intent is genuinely missing.
2. Use `start_orchestration` and the active public tool registry. Do not
   reproduce any call shape or recovery payload in prompts or prose.
3. Invoke every returned native dispatch exactly as returned before waiting. A
   dispatch authorizes only that spawn, not a wait or a successor.
4. Request one explicit 300-second native wait on the exact same eligible live
   child; never use the native default wait duration. If it returns no marker,
   request another explicit 300-second native wait on that exact child, and
   repeat until its message carries either a durable-question marker or
   terminal-completion marker. A timeout, no child message, partial output,
   unknown identity, or another child's event leaves that child live. A wait
   timeout or missing child message does not authorize `read_worker_wave`,
   continuation, any result read, replacement, or another lifecycle operation.
5. A durable-question marker authorizes only presenting the exact server-held
   question and ending the current turn for a real user answer. After that
   separate user message is recorded, resume the same child; it polls the
   durable answer and the coordinator returns to exact-child waiting. Only a
   terminal-completion marker authorizes reading the canonical current wave.
   Read it completely once eligible, then follow only its server-derived next
   dispatch, approval, handoff, retry, or terminal direction.

A worker uses `ask_worker_question` for one arbitrary-length Unicode question.
The coordinator uses `show_orchestration_question` to present it completely and
`answer_orchestration_question` to record one arbitrary-length Unicode user
answer, unless the answer was already supplied. The same child uses
`poll_worker_question`; its model decides whether the answer addresses the
question. Never remove or recreate the exchange or replace the child.

## Completion and compaction

Native child messages are status, not durable completion evidence. Complete only
after canonical results are read and the service supplies durable close evidence
and a terminal lifecycle direction. Do not manufacture a successor dispatch,
result reference, worker, or continuation.

At compaction, retain private coordinator authority, the server-derived step,
pending native identities, and canonical result references in the bounded private handoff.
If any authority is absent, stop rather than inspect, recover, or reconstruct it.
