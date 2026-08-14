---
name: orchestrator
description: Coordinate non-trivial coding work or source-backed repository knowledge harvesting with Codex custom agents. Use for C2/C3 features, debugging, reviews, migrations, Cortex help, incremental harvests, full harvest refreshes, or work that needs planning, delegated investigation, verification, and an evidence-based final integration.
---

# Cortex Orchestrator

## Invocation and routes

In Codex Desktop use the Skills picker to select `cortex:orchestrator` or
mention `$cortex:orchestrator`. In CLI use `$cortex:orchestrator` or `/skills`.
Bare `/cortex` and `/normal` are textual shorthand, not registered native slash
commands. Do not use the deprecated `/prompts` mechanism.

| Exact argument | Route | Effect |
| --- | --- | --- |
| `empty` | `orchestrate` | Start normal relative orchestration. |
| `help` | `help` | Explain Cortex without writes. |
| `harvest` | `harvest` | Incrementally synchronize knowledge docs. |
| `harvest-refresh` | `harvest-refresh` | Fully re-audit knowledge docs. |
| `normal` | `normal` | Exit the active Cortex session. |

Do not guess unknown arguments. Show help and ask the user to choose.

The help route explains invocation, opt-in behavior, the project-local
`.codex/cortex` ledger, the three v3 public tools, internal workers, and that
source/tests outrank generated docs. Help performs no activation, dispatch, or
write.

Non-help, non-`normal` routes explicitly authorize durable orchestration.
Ordinary work never activates Cortex. The normal route uses
`manage_orchestration` with intent `deactivate` only when a Cortex task is
active.

## Relative one-call-per-wave workflow

1. Form the task objective, success criteria, constraints, paths, approval
   boundaries, and user language. Usually let Cortex use its safe C2 default.
2. Call `start_orchestration` with exact absolute `project_root` and the task.
   Omit waves for the standard pipeline. A compact override is
   `{waves: [{workers: [{phase, ...}]}]}`; only phase is required.
3. Invoke each returned `{worker, call, arguments}` exactly. Native arguments
   are already filtered. Do not add IDs or turn expected model metadata into a
   native model override.
4. Wait for the complete wave. Workers use the native parent channel and
   return the strict eight-section `cortex/report/v1`; they never call Cortex.
5. Call `continue_orchestration` once with `project_root`, returned relative
   `step`, and results. Omit worker for one result; repeat the returned integer
   worker slot for parallel results.
6. Repeat until `outcome: completed`. If evidence changes future scope, send a
   compact `future_waves` replacement in the same continue call. Set
   `rework: true` only for intentional repetition of a completed phase.

Normal flow uses no caller-generated submission/task/wave/attempt IDs, no
coordinator identity, and no echoed host tool/model/effort. A relative `step`
is required only to separate retries from an identical report used on a later
wave. Durable identities, receipts, evidence, verification, manifest, and
handoff stay private in the compatible v7 ledger.

When several tasks are active Cortex returns `needs_selection` with objective
and opaque `task_ref`; use the chosen ref only for the next ambiguous or
recovery call. Use `manage_orchestration` for inspect, resume, deactivate,
lane, resource, or a durable MCP UI question.

## Luna high and dispatch contract

This contract is designed for a Luna high parent: two narrow normal tools,
relative slots only for parallel waves, aliases at runtime rather than schema
enums, and compact dispatches containing real native arguments only.
Configured-default Luna dispatches omit native `model` while preserving
reasoning effort. Explicit model overrides retain `model`. Expected routing is
not host attestation; claim the actual model only from host runtime metadata.

Use bundled profiles by exact name. Workers remain internal, English-only,
bounded to ownership and allowed paths, and cannot subdelegate without explicit
authorization. The main coordinator alone communicates with and localizes for
the user.

## Reports, questions, and completion

Every successful result contains exactly `summary`, `findings`, `questions`,
`changed_files`, `tests`, `evidence`, `uncertainty`, and `next_action`.
Non-success omits report and carries status plus reason. Cortex validates all
parallel slots and reports before task-state writes and preserves quotas,
redaction, one-use receipts, documentation/close, rework invalidation, and
manifest-backed handoff.

Questions normally return through the native parent channel. For a durable UI
prompt, call `manage_orchestration` with intent `question`; Cortex projects it
through MCP `elicitation/create` when the host advertises support. Never answer
on the user's behalf if the host cannot render elicitation.

Finish only after `outcome` is `completed` and report the verified handoff and
any live-evaluation limitations plainly.
