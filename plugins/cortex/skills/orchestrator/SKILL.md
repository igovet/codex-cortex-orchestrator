---
name: orchestrator
description: Coordinate non-trivial coding work or source-backed repository knowledge harvesting with Codex custom agents. Use for C2/C3 features, debugging, reviews, migrations, Cortex help, incremental harvests, full harvest refreshes, or work that needs planning, delegated investigation, verification, and an evidence-based final integration.
---

# Cortex Orchestrator

## Native invocation and route selection

In Codex Desktop, select Cortex Orchestrator (`cortex:orchestrator`) through
the Skills picker or mention `$cortex:orchestrator`. In Codex CLI, lead with
`$cortex:orchestrator`, or use `/skills` and select `cortex:orchestrator`.
Cortex does not register native bare `/cortex` or `/normal` commands; they are
textual shorthand and not registered native slash commands.
Do not use the deprecated `/prompts` mechanism.

Select exactly one route from the normalized argument:

| Exact argument | Route | Effect |
| --- | --- | --- |
| `empty` | `orchestrate` | Default task orchestration. |
| `help` | `help` | Explain usage without creating a ledger. |
| `harvest` | `harvest` | Incrementally synchronize missing or stale knowledge docs. |
| `harvest-refresh` | `harvest-refresh` | Fully re-audit knowledge docs from source evidence. |
| `normal` | `normal` | Exit an active Cortex session without creating a task. |

Do not guess unknown arguments. Show the help route and ask the user to choose.

### Help route

Report these facts concisely:

- Desktop uses `cortex:orchestrator` or `$cortex:orchestrator`; CLI uses
  `$cortex:orchestrator` or `/skills`.
- `/cortex` and `/normal` are textual shorthand, not
  registered native slash commands.
- Orchestration is opt-in and stores a project-local `.codex/cortex` ledger.
- Cortex v2 exposes one MCP tool, `orchestrate`, and advances one dependent
  worker wave per call.
- Workers remain internal and source/tests outrank generated documentation.

Do not activate Cortex, initialize a task, dispatch a worker, or write project
files merely to display help.

## Explicit activation

The non-help, non-`normal` routes explicitly authorize durable orchestration.
Ordinary requests, complexity, or ordinary subagent use do not activate it.
The `normal` route calls `orchestrate(operation="deactivate")`; it creates no
task.

Every Cortex call includes the exact absolute `project_root`. The server may
serve multiple projects in one process, but task identity remains bound to the
project-local ledger, coordinator principal, and thread. A missing or
unwritable root, `CORTEX_ROOT`, or `/tmp` fallback is a blocker.

## One-call-per-wave workflow

1. State the goal, success criteria, constraints, paths, approval boundaries,
   and user language. Classify the work as C1, C2, or C3.
2. Build the complete ordered `waves` plan. Each wave contains only independent
   gates/agents. Keep conflicting writers and dependent decisions in separate
   waves. Cortex appends mandatory documentation and close waves when absent.
3. Call `orchestrate(operation="start")` once. Pass a stable submission id,
   task contract, full waves, coordinator identity, and exact native model
   catalogs. Do not call private activation, classification, status, or
   delegation functions.
4. Invoke every returned native `spawn_request`. Use the exact host tool,
   profile name, reasoning effort, prompt/message, and optional thread
   environment. For `model_resolution = "configured_default"`, preserve the
   omitted `model` key so Codex resolves
   `agents.default_subagent_model = "gpt-5.6-luna"`; use `expected_model` only
   as request metadata and verify the actual child settings independently.
   Pass only keys that are real arguments of the returned native request:
   never copy `expected_model` into `spawn_agent.model`.
   Workers do not call Cortex; they return their strict report in the native
   parent/child result.
5. Wait for every worker in the wave. Route questions through the native
   parent channel and main chat. Use `operation="question"` only when a
   durable question record is required.
6. Call `orchestrate(operation="advance")` once with all completions. Passed
   completions include the actual host identity/model/effort and exactly eight
   report fields: `summary`, `findings`, `questions`, `changed_files`, `tests`,
   `evidence`, `uncertainty`, and `next_action`.
7. Spawn the returned next wave and repeat. If evidence changes scope, include
   a complete `future_waves` replacement in the same `advance` call. Use
   `allow_rework=true` only for intentional completed-gate rework.
8. Finish only after the final `advance` returns `state="completed"`. Cortex
   internally reconciles reports, evidence, documentation, verification,
   changed files, handoff, and close invariants.

Normal orchestration must use only `start` and `advance`. `inspect`, `resume`,
and `deactivate` are recovery/session operations; `lane`, `resource`, and
`question` are uncommon nested modes. Never retry through removed legacy tool
names.

## Dispatch and worker contract

Use the bundled profiles (`explorer`, `planner`, `architect`, `general`,
`qa_engineer`, `code_reviewer`, `security_auditor`, `technical_writer`, and
specialists). The coordinator owns scope and integration. Workers own only
their declared paths and acceptance criteria, do not subdelegate without
explicit authorization, and emit English only.

Model choice is per delegation. Luna handles reading, discovery, research,
review, CRUD work, and small fixes; Terra handles broader implementation,
architecture, migration, and debugging; security routes start from Sol. The
runtime's exact remapping table and host capability checks are authoritative.
Never claim a requested or expected model was used when the host returned
another model. Explicit Terra/Sol/Luna requests retain a native `model`
override; configured-default Luna requests omit it and always carry an
explicit reasoning effort.

The worker's final report is not user-facing. The coordinator translates and
integrates it. A worker without a strict report cannot be passed; submit a
terminal non-success completion with an explicit reason.

## Recovery

- Replay an identical mutating request with the same `submission_id`; a changed
  request uses a new id.
- Use `inspect` after interruption. It reconstructs a facade plan for existing
  v7 tasks when necessary.
- Follow `ok=false`, `diagnostics`, and `next_action`; do not invent receipts,
  attempts, or identities.
- Use `resume` only after resolving a recorded blocker.
- Use `lane` and `resource` modes for durable worktrees or exclusive resources,
  with expiries and clean release.

Before compaction or a user-facing final response, preserve the exact task id,
wave, attempts, decisions, changed paths, verification results, risks, and next
action. Do not include secrets, raw prompts, or private telemetry.

## Knowledge routes

For `harvest` and `harvest-refresh`, also follow the bundled
`knowledge-harvest` skill. Represent discovery, writing, verification,
documentation, and close as normal Cortex waves. Incremental harvest changes
only stale or missing generated facts. Refresh re-audits source-backed facts,
preserves manual text outside generated blocks, and verifies an idempotent
second pass.
