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

After every returned `spawn_agent` or same-child `followup_task`, preserve the
exact child identifier returned by the host and immediately use `wait_agent`
for that exact bound child. Do not call `read_worker_wave` until `wait_agent`
reports every child from that dispatch terminal and the lifecycle response
permits the read.
`No agents completed yet`, timeout, an empty completion set, `pendingInit`,
`running`, or a still-working child requires another wait for the same exact
child identifiers and authorizes no Cortex call. Terminal means only
`interrupted`, `completed`, `errored`, `shutdown`, or `notFound`.
When Cortex returns a technical-recovery dispatch, invoke only that native
dispatch. The bound worker consumes every server-supplied source and recovery
chain result before project work. An eligible same-child supplement is bounded
to one returned follow-up turn; any later fallback is a new server-issued
dispatch, never a coordinator-invented replacement.

The root does not create visible threads, run another execution layer, use
collaboration calls other than the returned native dispatches, exact-bound-child waits,
and authorized same-worker follow-up, or substitute a local implementation for
a missing or failed worker. Native V2 dispatch, repeated exact-bound-child wait cycles,
and authorized same-worker follow-up are the only worker lifecycle primitives.

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
asks for full governance. `minimal` requests the lowest governance baseline,
not absence of governance; the service promotes it when complexity or risk
requires deeper governance. Do not make an unstated hidden-risk assessment on
the user's behalf.

## Worker model selection

<!-- BEGIN GENERATED CORTEX MODEL ROUTING -->
C1, C2, and C3 select the task governance baseline only; they do not select one model or effort for every worker.

The coordinator chooses model and reasoning effort independently for every worker. Prefer Luna by default, Terra only for genuinely complex non-security work, and recommend Sol only for security work; explicit supported coordinator choices remain authoritative.

| Exact model | Recommended effort | Recommend for |
| --- | --- | --- |
| `gpt-5.6-luna` | `high` | Default bounded work, including discovery, ordinary implementation, QA, and deterministic rechecks. |
| `gpt-5.6-terra` | `high` | Genuinely complex non-security implementation, cross-cutting analysis, or demanding review. |
| `gpt-5.6-sol` | `high` | Security profiles and security work only. |
<!-- END GENERATED CORTEX MODEL ROUTING -->

An initial worker uses the coordinator-selected exact model and
reasoning-effort values. Technical recovery is server-owned and bounded for
the exact failed occurrence: Luna advances to Terra, Terra advances to Sol,
and a Sol specialist may advance once to the operation-compatible universal
profile before terminal exhaustion. Product rework never consumes this ladder.

## Worker capability selection

<!-- BEGIN GENERATED CORTEX PROFILE CAPABILITIES -->
| Operation kind | Meaning |
| --- | --- |
| `inspect` | Read and analyze bounded project evidence without changing project files. |
| `modify` | Change bounded project artifacts and verify the scoped mutation. |
| `verify` | Independently evaluate acceptance or findings without changing project files. |
| `close` | Perform terminal non-mutating closure against complete predecessor evidence. |

| Profile | Allowed operation kinds |
| --- | --- |
| `accessibility_auditor` | `inspect`, `verify` |
| `accessibility_fixer` | `inspect`, `modify`, `verify` |
| `architect` | `inspect` |
| `backend_dev` | `inspect`, `modify`, `verify` |
| `build_verification` | `inspect`, `verify`, `close` |
| `code_reviewer` | `inspect`, `verify`, `close` |
| `data_engineer` | `inspect`, `modify`, `verify` |
| `database_architect` | `inspect`, `verify` |
| `debugger` | `inspect`, `modify`, `verify` |
| `devops_engineer` | `inspect`, `modify`, `verify` |
| `explorer` | `inspect` |
| `frontend_dev` | `inspect`, `modify`, `verify` |
| `fullstack_dev` | `inspect`, `modify`, `verify` |
| `general` | `inspect`, `modify`, `verify` |
| `mobile_dev` | `inspect`, `modify`, `verify` |
| `performance_engineer` | `inspect`, `verify` |
| `planner` | `inspect` |
| `qa_engineer` | `inspect`, `modify`, `verify` |
| `refactorer` | `inspect`, `modify`, `verify` |
| `security_auditor` | `inspect`, `verify` |
| `technical_writer` | `inspect`, `modify`, `verify` |
| `ux_designer` | `inspect` |
<!-- END GENERATED CORTEX PROFILE CAPABILITIES -->

Choose each worker's operation capability from the intended work,
independently of semantic phase sequencing. A phase never grants file
authority. Before persistence, the backend compiles the selected profile and
operation through the canonical capability registry. It normally resolves a
mismatch deterministically to one compatible same-family, phase-aligned, or
general profile and projects the requested choice, resolved choice, and reason.
It rejects without creating a dispatch only when that canonical resolution is
unavailable or ambiguous. The worker briefing receives only the compiled
operation and capability.

Invoke every returned native dispatch with its complete arguments unchanged.
The worker uses the exact `CORTEX_DISPATCH_REF` first line from that unchanged
message as its `read_dispatch_briefing` authority and never infers or rewrites
it.
Terra and Sol dispatches carry a mandatory native model override; never omit or
change it. Luna intentionally uses the verified configured native default and
therefore omits the override. A model-attestation failure cannot be repaired by
resuming the same child; use only a newly server-issued replacement dispatch,
or fail closed when none is returned.

When a worker reports a dispatch-authority failure, follow only the exact
server-returned same-worker recovery. If its first operation arrived before the
trusted spawn observation, the worker may retry only that operation with
bounded backoff until a finite deadline. It must not access the project, switch
operations, reconstruct authority, or spawn a replacement. At the deadline or
after any later failure, follow returned fail-closed recovery; terminal child
prose does not authorize a coordinator operation.
An exact successful retry automatically clears the transient observer failure;
do not add a separate clearing operation or inspect private state.

## Normal lifecycle

The complete lifecycle is defined once in `../cortex-control/SKILL.md`; this
skill supplies only coordinator routing. Preserve the user's material request
through the host input surface, start through the active registry, and execute
every returned native V2 dispatch before exact-bound-child waits. Follow the control
skill's wait, question, terminal Stop, canonical-read, future-wave revision,
and governance-closure sequence exactly. Do not reproduce call shapes,
response fields, repair payloads, or alternate lifecycle prose here.

## Completion and compaction

Use the control skill's completion gate: canonical terminal result, exact
terminal Stop, complete result read, required governance closure, and the
service-supplied terminal direction. Native child prose and wait output
are never evidence. Never manufacture a successor dispatch, result reference,
worker, observation, or continuation, or inspect private state to recover one.

At compaction, retain private coordinator authority, the server-derived step,
pending native identities, and canonical result references in the bounded private handoff.
After a same-incarnation compaction, clear, or reset, make the complete
paginated `inspect_orchestration` read the first Cortex lifecycle operation;
no earlier page authorizes waiting, replay, follow-up, continuation, resume,
or child creation.
If any authority is absent, stop rather than inspect, recover, or reconstruct it.
