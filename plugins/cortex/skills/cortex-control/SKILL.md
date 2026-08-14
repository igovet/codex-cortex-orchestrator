---
name: cortex-control
description: Use this skill when coordinating a non-trivial task across Codex agents and durable gate, delegation, lock, or handoff state is useful. It uses the local cortex MCP server; choose each subagent's model and reasoning effort dynamically at dispatch time.
---

# Cortex Control

Cortex v2 exposes exactly one MCP tool: `orchestrate`. Its private v7
lifecycle primitives are implementation details and must never be called by a
coordinator or worker. Ordinary requests do not activate Cortex; a non-help,
non-`normal` `cortex:orchestrator` route is still required.

## Normal flow

1. Build the complete task contract and ordered waves. A wave may contain
   multiple independent gates or multiple independent workers for one gate.
   Serialize conflicting writers and dependent gates.
2. Call `orchestrate(operation="start")` once with:
   - a stable lowercase `submission_id`;
   - the exact absolute `project_root`, coordinator `principal`, and
     `thread_id`;
   - `task` containing `task_id`, objective, complexity, requirements,
     acceptance criteria, scope, allowed paths, and verification;
   - the full `waves` array;
   - `host_capabilities.spawn_agent_models` and the confirmed
     `host_capabilities.spawn_agent_default_model` when the fresh host loaded
     one. The installer configures
     `agents.default_subagent_model = "gpt-5.6-luna"`; do not add a Luna
     override to a normal hidden `spawn_agent` request when the route reports
     `model_resolution = "configured_default"`.
3. Invoke every returned `spawn_request` with the native host tool. Workers do not call Cortex.
   They use the native parent/child channel for questions and
   return one strict `cortex/report/v1` object in their final response.
4. After every worker in the wave reaches a host-terminal state, call `orchestrate(operation="advance")` exactly once
   with the active `task_id`,
   `wave_id`, a new stable `submission_id`, and every completion. A passed
   completion includes the actual host id/tool/model/effort (the actual model
   is required even when the request omitted `model`) and exactly these
   report keys: `summary`, `findings`, `questions`, `changed_files`, `tests`,
   `evidence`, `uncertainty`, and `next_action`.
5. Invoke the next returned spawn requests and repeat. The final `advance`
   performs evidence linking, documentation decision repair, server-observed
   close verification, manifest reconciliation, handoff, report-bus audit,
   and task completion internally.

Never reconstruct the old activation/classification/status/delegation/report/
evidence/gate sequence. Legacy tool names are removed from the public MCP API.

## Exact request contract

The coordinator must construct every field itself; do not improvise aliases or
old v7 shapes. A `start` request contains `operation`, `project_root`,
`principal`, `thread_id`, `submission_id`, `host_capabilities`, `task`, and
`waves`. `task` requires `task_id`, `objective`, and `complexity` (`C1`, `C2`,
or `C3`), plus the documented requirements, acceptance criteria, scope,
allowed paths, verification, budget, pause conditions, user language, and
replan limit when applicable. `host_capabilities` requires the exact native
`spawn_agent_models` catalog and may include the confirmed
`spawn_agent_default_model`, `create_thread_models`, and
`available_thread_models` catalogs.

Each wave requires `{wave_id, delegations}`. Each delegation uses `gate` and may
specify `agent`, `task_kind`, `risk`, model/effort routing, dispatch mode,
ownership, objective, allowed paths, acceptance criteria, verification, and
context fields. The old `{id, gates: [{id, owner}]}` shape is invalid: use
`wave_id`, `delegations`, `gate`, `agent`, and `ownership`.

An `advance` request requires `task_id`, `wave_id`, and one completion object
per active attempt. Every completion carries the actual host tool, child id,
task name, model, reasoning effort, terminal status, and the exact eight-field
`cortex/report/v1` report when passed. All mutating operations require a fresh
lowercase `submission_id`; only byte-identical retries reuse an id.

The facade performs an aggregate preflight. If several fields are wrong, the
single `ok: false` response contains every diagnostic with its `path`,
`message`, and `expected` shape. Correct all listed paths before retrying; do
not fix one error at a time or reuse the failed submission id.

## Recovery and adaptation

- `inspect` is read-only and reconstructs the active wave, pending host
  requests, and compact task state, including for a v7 task created before the
  facade plan existed.
- `resume` continues a blocked task and prepares its current wave.
- `deactivate` exits the explicit Cortex session after completion.
- An identical mutating retry reuses its `submission_id`; Cortex returns the
  committed result. A changed payload must use a new id.
- Supply `future_waves` to `advance` to replace only not-yet-started work.
  Reintroducing a completed gate requires `allow_rework=true`.
- Follow structured `ok=false`, `diagnostics`, and `next_action` results. Do
  not fall back to private tools or create an unledgered attempt.

## Rare operations

The same public tool contains the uncommon subsystems:

- `operation="lane"` with payload commands `create`, `inspect`, `claim`,
  `release`, `retire`, `bind_task`, `materialize`, `reconcile`,
  `claim_resource`, or `release_resource`;
- `operation="resource"` with `claim`, `release`, `acquire_lock`, or
  `release_lock`;
- `operation="question"` with `ask`, `publish`, `list`, `answer`, or
  `updates` when a question must be durable instead of using the native
  parent/child channel.

Mutating rare operations also require a stable `submission_id`.

## Dispatch policy

Profiles do not pin model or reasoning effort. Cortex resolves each delegation
against the supplied host catalogs. Luna handles explicit reading, discovery,
research, review, CRUD-level edits, and small fixes. Terra handles other
implementation, architecture, migration, and debugging work. Security routes
start from Sol. The exact model/effort remapping table in the runtime remains
authoritative; never relabel the actual host model. A configured-default Luna
route carries `expected_model = "gpt-5.6-luna"`,
`model_resolution = "configured_default"`, and an explicit
`reasoning_effort`, while omitting native `model`. Explicit Terra/Sol/Luna
overrides retain a `model` field. Confirm and advance using the runtime-
reported effective model, not request metadata.

When the configured default is not confirmed, Cortex uses an explicit Luna
override if native `spawn_agent` advertises Luna. Otherwise it emits an
explicit hidden Terra override. Automatic `create_thread` fallback is
forbidden; a visible task may exist only through a separate explicit
`dispatch_mode = "visible_thread"` request.

Workers emit English only. The main coordinator alone localizes questions,
blockers, and final summaries for the user. Worker prompts, reports, and ledger
state must not contain secrets or personal data.

## Completion contract

Wait for every dispatched host worker in the current wave. Never advance with
an unexplained running attempt. The facade preserves report receipts, linked
evidence, terminal non-success reasons, documentation and reassessment
receipts, successful close verification, the final file manifest, and the
close handoff.

Only after `orchestrate` returns `state="completed"` may the coordinator report
`PASSED`. A `blocked` or failed result must identify the affected attempts,
changed files, exact checks, and limitations.

## Durable artifacts

Runtime state remains below `${project_root}/.codex/cortex` using schema
`cortex/v7`. `orchestration.json` stores the facade waves and `operations/`
stores idempotent transaction receipts. Every call carries its own absolute
project root; one MCP process may safely serve multiple projects. `CORTEX_ROOT`
and `/tmp` fallbacks remain forbidden.
