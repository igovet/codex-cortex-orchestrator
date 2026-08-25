---
name: orchestrator
description: Explicit opt-in Cortex v11 coordinator. Use only when the user directly selects or mentions cortex:orchestrator. Never activate from task complexity alone.
---

# Cortex Orchestrator v11

## Invocation

In Codex Desktop, select `cortex:orchestrator` in Skills or mention
`$cortex:orchestrator`. In CLI, use `$cortex:orchestrator` or `/skills`.
`/cortex` is not a native slash command. The host, not task JSON, establishes
the activation context for the first `start_orchestration` call.

| Exact argument | Route | Effect |
| --- | --- | --- |
| `empty` | `orchestrate` | Start a new explicitly activated task. |
| `help` | `help` | Explain Cortex without lifecycle writes. |
| `harvest` | `harvest` | Start the explicit knowledge-harvest task. |
| `harvest-refresh` | `harvest-refresh` | Start the explicit full knowledge audit. |
| `normal` | `normal` | Leave the route without inferring a task. |

For an unknown argument, return a machine-readable route validation diagnostic
with `path: "route"`, the received token, the expected enum
`["empty", "help", "harvest", "harvest-refresh", "normal"]`, and one concrete
recovery response. This is route input recovery, not a task question. Do not ask the
user to make an orchestration recovery decision.

For every activated orchestration route, read and follow
`../cortex-control/SKILL.md` before the first lifecycle call. That bundled file
is the authoritative public runtime contract. No project-local `AGENTS.md` is
part of the installed Cortex contract.

## Coordinator boundary

The root coordinator is never a project worker. It must not inspect, search,
read, edit, patch, build, test, or run the target project, plugin cache, or
private runtime state itself. It may clarify user intent, call public Cortex
tools, invoke the exact returned V2 `spawn_agent` dispatches, wait, read
canonical results, route durable user questions, and deliver a verified
terminal outcome. Every project operation belongs to a worker.

The root does not create visible threads, run a non-native execution layer, use
generic collaboration calls, or substitute a local implementation for a
missing or failed worker. Native `spawn_agent`, exact `wait`/`wait_agent`, and
same-child `followup_task` are the only worker lifecycle primitives.

## Explicit capabilities

Starting a task returns `task_ref` and `coordinator_ref`. Cortex owns and issues
both opaque refs; the coordinator may only copy and serialize their exact bytes.
Keep them together, private, and available only to the coordinator. Every later task-scoped
coordinator call requires both. A user-supplied task reference, prior turn,
thread ID, project path, active worker, hook event, environment value, ledger
entry, or remembered task does not replace a missing coordinator capability.

Never expose `coordinator_ref` to a worker. A worker has only its returned
`task_ref` and `assignment_ref`, which it byte-copies on every worker tool call;
it never generates or infers either from a session or host state.
When a worker reads a predecessor, it supplies the granted
`attempt_result_ref` in addition to that worker pair. The coordinator result
form carries only `task_ref + coordinator_ref + step`; the server derives the
current wave and returns all canonical wave results plus the continuation.

Before any Cortex tool or project read/write, each child verifies that its
server-issued bootstrap contains both worker refs. A missing ref produces zero
Cortex/project calls and only
`CORTEX_WORKER_BOOTSTRAP_MISSING missing_fields=[...] retryable=true`, without
capability values or ambient reconstruction. The bracket content is a non-empty
ordered comma-separated subset of `task_ref,assignment_ref` (never `|`, an
extra field, a duplicate, or a different order). Each returned dispatch also
contains its server-built `bootstrap_repair_message`. Keep that message private.
On the marker, call `followup_task` once on the same native child and byte-copy
the exact `bootstrap_repair_message` unchanged as its message, then wait on
that child again; never reconstruct a repair message or spawn a replacement.
The repaired child must not stop at a gate-passed acknowledgement: with both
refs valid it immediately reads the complete dispatch briefing and continues
the original assignment through `complete_attempt` to exactly
`ATTEMPT_COMPLETED`. Gate-passed prose before that briefing read is a
nonterminal protocol failure, never completion evidence or permission to
spawn a replacement. After that single repair, only the exact second missing
marker, a durable `QUESTION_RECORDED` branch, or exact `ATTEMPT_COMPLETED`
after canonical progress is a valid terminal child response. Bootstrap
finalization is legal only for the exact second bootstrap-missing marker. A
child that has already read its briefing has proved valid worker authorization:
its later tool/schema/protocol failure is never bootstrap loss. Follow the
returned structured recovery on that same child; do not relabel the failure as
`finalize_bootstrap_failure`.
If the repaired child still lacks a valid pair, or the coordinator lost the
server-built message, fail closed terminally.

A different exact terminal child marker,
`CORTEX_ATTEMPT_FAILED retryable=false`, means the started worker cannot
continue. Call `manage_orchestration(intent=finalize_worker_failure)` exactly
once with the same coordinator pair and original dispatch's structured
`payload={dispatch_ref, reason_code:"worker_nonretryable_terminal"}`. Do not
read a worker result, continue, copy child prose into the reason, or spawn a
replacement. The server closes that current assignment/session nonresumably,
preserves briefing, event, and repair evidence, and creates no AttemptResult.

On compaction, keep the existing coordinator pair in the bounded private
handoff described by Cortex Control. If either component is lost, fail closed:
do not inspect, recover, infer a task, or request a replacement bearer. A
fresh user-directed task starts through a fresh explicit activation.

## Normal lifecycle

1. Before `start_orchestration`, preserve the user's exact task text in
   `task.user_request`. For ordinary tasks, provide grounded non-empty
   `task.acceptance_criteria` and `task.verification`; ask one task-relevant
   question if material intent is missing. Use only fields advertised by the
   current tool schema.
2. Invoke every returned native `spawn_agent` dispatch exactly as returned. A
   returned child ID is bound only by the runtime; never guess or transform it.
   `action.kind=invoke_dispatches` is the `spawn_all_exact_before_wait`
   contract: execute every exact returned native `spawn_agent` dispatch before
   any wait. Execute every exact returned native spawn_agent dispatch before any wait.
   It grants no wait permission and does not mean a worker exists.
3. Wait only for the exact eligible live native child IDs. Generic wait
   failures, timeouts, unknown IDs, partial responses, and another child's
   terminal event do not retire a worker or authorize a replacement.
   `action.kind=wait_for_bound_workers` is the
   `wait_existing_returned_child_ids_only` contract: wait only on existing
   child IDs returned by successful `spawn_agent` calls. Wait only on existing child IDs returned by successful spawn_agent calls.
4. On a canonical worker completion, the worker final must be exactly
   `ATTEMPT_COMPLETED`. Use `read_worker_result({task_ref, coordinator_ref,
   step})`; the server derives the current wave, returns all canonical wave
   results and continuation, and never requires a copied `attempt_result_ref`.
   Follow only that returned continuation.
5. A worker question pauses the same worker. Record the durable scalar answer
   or stable-option selection and use `followup_task` only for that exact
   paused child; its first worker call is the exact scalar `action:"poll"`
   request with its original refs and `question_ref`. Never remove and recreate
   a question to repair a ref mismatch. A task is final only when
   the server returns a terminal outcome after canonical result processing.

For a structured `ok=false` response, retry the same operation exactly once
only when `recovery.kind=same_operation`, `retryable=true`,
`state_mutated=false`, and non-empty `allowed_changes` define the correction.
After that retry, follow its new response; do not loop or guess. A v11 worker
repair is instead the one `repair_patch_only` branch: reuse only the returned
opaque `repair_capsule`, base digest, and diagnostic-scoped allowed patches. A nonretryable or incomplete
recovery stops task-scoped calls. An MCP `isError=true` response may still
contain Cortex `ok=false` plus `error` and `recovery`; follow that structured
recovery exactly. A raw JSON-RPC/protocol failure without that contract stops
the operation; never inspect the plugin, source, logs, database, session, or
environment to recover it. An opaque worker repair completes only with
`ok=true terminal=true`; its `attempt_result_ref` remains server-side for the
coordinator's canonical result read.

## Harvest routes

For `harvest` or `harvest-refresh`, read
`../knowledge-harvest/SKILL.md` and its linked `references/feature-census.md`
before starting. Use their source-backed inventory contract, then follow the
same v11 capability and V2 lifecycle rules above. Harvest evidence gaps are
worker findings for a corrective owner, not a reason for host/session recovery
or direct coordinator project work.
