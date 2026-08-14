---
name: orchestrator
description: Coordinate non-trivial coding work or source-backed repository knowledge harvesting with Codex custom agents. Use for C2/C3 features, debugging, reviews, migrations, Cortex help, incremental harvests, full harvest refreshes, or work that needs planning, delegated investigation, verification, and an evidence-based final integration.
---

# Cortex Orchestrator

## Native invocation and route selection

In Codex Desktop, direct the user to select Cortex Orchestrator
(`cortex:orchestrator`) through the Skills picker or mention
`$cortex:orchestrator`. In Codex CLI, direct the user first to
`$cortex:orchestrator`, or to `/skills` and then select
`cortex:orchestrator`. Cortex does not register native bare `/cortex` or
`/normal` commands: they are not registered native slash commands. Those and
their arguments are optional textual shorthand only when the host passes them
through instead of reserving them. Do not use the deprecated `/prompts`
mechanism.

Select exactly one route from the normalized argument after the skill name or
the `/cortex` text prefix:

| Exact argument | Route | Effect |
| --- | --- | --- |
| `empty` | `orchestrate` | Default normal task orchestration. |
| `help` | `help` | Explain usage and return without creating a ledger. |
| `harvest` | `harvest` | Incrementally synchronize missing or stale knowledge docs. |
| `harvest-refresh` | `harvest-refresh` | Fully re-audit knowledge docs from source evidence. |
| `normal` | `normal` | Exit an active Cortex session without creating a task. |

Do not guess unknown subcommands. Show the `help` route and ask the user to
choose. Only exact lowercase route names are defined.

### Help route

Report these facts concisely:

- Desktop: choose Cortex Orchestrator (`cortex:orchestrator`) in the Skills
  picker or send `$cortex:orchestrator`; CLI: send `$cortex:orchestrator` or
  use `/skills` then select `cortex:orchestrator`. Append one exact argument
  from the table when needed.
- Textual shorthand: `/cortex`, `/cortex help`, `/cortex harvest`,
  `/cortex harvest-refresh`, and `/normal` are interpreted text, not
  registered native slash commands, and a host may reserve or reject them.
- `orchestrate` is the default task route; `harvest` is incremental;
  `harvest-refresh` is a full source-backed re-audit; `normal` exits an active
  orchestration session.
- Cortex uses a project-local `.codex/cortex` ledger only after explicit
  activation. Workers are hidden, local control receipts do not prove a host
  spawn, and source/tests outrank generated documentation.

The help route is read-only. Do not activate Cortex, initialize a task, create
a lane, dispatch a worker, or write project files merely to display help.

## Explicit activation

The main/root Codex agent is the sole user-facing coordinator. Selecting this
skill for `orchestrate`, `harvest`, or `harvest-refresh` explicitly activates
the workflow. Selecting `normal` deactivates the active session and creates no
task. `/cortex` and `/normal` may be treated as text only if the host passes
them through; do not claim that either is a native slash command. Ordinary
requests, complex requests, requests that merely mention orchestration, and
ordinary use of subagents never activate durable orchestration implicitly.

After activation, the main agent calls the control plane directly before
`init_task`. It passes the exact absolute `project_root` on activation, which
immutably binds the MCP process; later calls may repeat it or let the process
restore it. It
must first complete `activate_orchestration` → `classify_task` → `init_task` →
`get_task_status`. Until status confirms both that exact root and
`${project_root}/.codex/cortex`, no project search, read, edit, command, test,
or worker dispatch may occur. MCP absence, any failure, an unwritable ledger,
a mismatched root, `CORTEX_ROOT`, or a `/tmp` fallback is a hard blocker: stop
and report it; never continue through an ordinary-subagent or unledgered
workflow. It may then coordinate, delegate, inspect worker reports, advance
gates, and hand off, but project search, reads, execution, tests, builds, and
edits belong to internal workers. Workers are never user-facing: all
clarifications, escalations, blockers, and handoffs return to the main chat.

The classification receipt is authoritative for initial complexity,
requirements, and pipeline. The main orchestrator must choose the complete
optional gate list from the available gates and pass it as `classify_task.pipeline`.
Use canonical IDs only: `plan`, `discover`, `architecture`,
`database_architecture`, `implementation`, `qa`, `security`, `performance`,
`accessibility`, `ux`, `review`, `documentation`, and `close`. Cortex accepts
the bounded compatibility aliases `planning`, `discovery`, and
`verification`, but the orchestrator must not emit those human labels.
When independent gates have no dependency or resource conflict, also pass
ordered `classify_task.parallel_groups` waves; gates in one wave may execute
concurrently and the next wave waits until every gate in the current wave is
resolved. Cortex still appends only the mandatory `documentation` and `close`
gates. Do not try
to reconstruct its pipeline in `init_task`; if a duplicate pipeline is supplied,
Cortex ignores it and returns `pipeline_correction` rather than failing or
weakening mandatory gates.

## Intake and dispatch

1. State the goal, acceptance criteria, constraints, affected area, and approval boundaries. Select the full initial gate list from Cortex's available gates and pass it to `classify_task.pipeline`; use the requirements as the evidence/rationale for that choice. Pass `parallel_groups` when two or more selected gates are independent; never group conflicting writers or gates with a dependency. Cortex will append only `documentation` and `close` when they are absent. If discovery changes the scope or dependencies, call `reassess_pipeline` with a new complete `pipeline` and (when needed) `parallel_groups`; apply it only after reviewing the proposed replacement, and set `allow_rework: true` when intentionally removing a completed gate.
2. Inspect relevant project and feature documentation when present; source and tests win if they conflict.
3. Classify by shape: C1 is local and low-risk; C2 spans a component or has uncertainty; C3 crosses systems, data, security, infrastructure, or has high rollback cost.
4. Choose models and reasoning effort per dispatch. Use one writer for overlapping paths, parallelize independent read-only work, and run independent verification before completion.

Before every native `spawn_agent` call, inspect the host tool's accepted model
values and pass the exact list as `available_models` when preparing the Cortex
delegation. If the lightweight Luna route is unavailable but Terra is accepted,
Cortex selects Terra and records `fallback_reason=host_model_unavailable` and
`fallback_from_model=gpt-5.6-luna`; the coordinator must report it as a Terra
worker. Other missing required routes fail closed. After every native
`spawn_agent` call, confirm the returned child with the
actual `host_model` and `host_reasoning_effort`, not only the child id. Cortex
requires the actual host model for model-routed attempts; a missing model is
recoverable, while a requested/actual mismatch (for example Luna requested but
Terra started) records `host_model_mismatch` and must not be reported as a
successful worker.

`visible_thread` is a separate, opt-in Desktop route, not a hidden-subagent
fallback. Use it only when the user explicitly asks to create a visible task
and the narrow work is eligible for Luna. Inspect native `create_thread`, pass
its exact model catalog as `available_thread_models`, and record
`dispatch_mode: visible_thread`; Cortex returns `spawn_request.host_tool` as
`create_thread`, with `prompt`, `title`, Luna, the dynamically selected
reasoning effort, and `thread_environment` (default `local`). Never force
`max`: keep the route's effort unless the task shape itself warrants a higher
one. Call `list_projects` before creating a repository task and map
`thread_environment` to the native target: pass `environment: {type: "local"}`
to use the saved checkout, or `environment: {type: "worktree"}` only when
isolation was explicitly requested. Local threads share files and uncommitted
changes, so serialize writers; worktrees remain the isolation option for
concurrent edits. Confirm the returned `threadId` through
`confirm_host_spawn` as `host_agent_id`, with `host_tool: create_thread`, then
monitor with `wait_threads`, read with `read_thread`, and send follow-ups with
`send_message_to_thread`. A visible task is user-owned and may appear in the
sidebar; it must not be created implicitly merely because `spawn_agent` lacks
Luna.

When the user explicitly requests that Luna be used through a task if native
`spawn_agent` cannot accept it, do not leave `dispatch_mode` at its hidden
default and accept Terra. Pass both host catalogs plus
`luna_fallback: visible_thread` to `record_delegation`. Cortex then keeps a
hidden Luna subagent when that tool supports Luna, but returns a `create_thread`
Luna request when it does not. This is the only automatic decision in that
explicitly authorized fallback mode.

Use only the 21 profiles declared in `plugins/cortex/profiles.json`;
`task_formatter` is retired. Record task kind, risk, complexity, requested
capability, and the resolved capability. With multi-agent v2 enabled, every
delegation is evaluated independently from its declared work intent and risk.
Use Luna for reading, discovery/data gathering, investigation, diagnosis,
research, code review, CRUD-level edits, and small fixes when `task_kind`
explicitly declares that intent, at any risk and regardless of the parent
task's C1/C2/C3 classification. A read-only profile alone does not imply Luna:
non-analysis work such as architecture, migration, debugging, or implementation
uses Terra.
Use canonical task kinds such as `discover`, `data_gathering`,
`runtime_investigation`, `diagnosis`, `research`, or `code_review` when the
worker is collecting facts or analyzing a bounded problem; do not hide that
intent behind a generic `implementation` task kind.
Security task kind,
the security gate, and the `security_auditor` profile always use Sol;
contradictory task kinds are normalized to security.
Non-security Sol requires a structured `sol_escalation`: either a supported,
auditable extreme criterion with an `audit_ref`, or a `terra_failure` linked to
a failed Terra attempt in the current ledger. Free-form `escalation_reason`
text is context only and cannot authorize Sol. Supported auditable-extreme
criteria are `irreversible_multi_system_recovery`,
`safety_critical_incident_response`, and
`novel_cross_system_failure_without_bounded_rollback`. For Luna
analysis/lightweight work, default/minimum reasoning is `medium` at
low/moderate risk, `high` at high risk, and `xhigh` at critical risk; explicit
higher requested effort is preserved. Otherwise reasoning is selected
independently of routing; requested effort `none` becomes `low`.

Internal worker execution is English-only: worker prompts, tool arguments,
reports, findings, question records, handoffs, and audit events must be in
English. The main coordinator owns user-facing language and must use the
language of the task or an explicit `user_language` when presenting questions,
blockers, decisions, and summaries. It may pass localized display fields to
`cortex.question`; durable English worker records remain authoritative.
This applies to every worker-emitted message, including commentary, progress
updates, and final answers. A visible Luna task remains an English-only
execution channel even though it appears in the sidebar; non-English task text
is input data, and only the main coordinator may localize it for the user.

For repository/code discovery, every worker must first call
`mcp__codebase_memory__list_projects`, match the exact absolute `project_root`
to an indexed project, and pass the returned project identifier/path to later
codebase-memory tools. Never guess the project identifier. Only after that
lookup, prefer `search_graph` for definitions and relationships, `search_code`
for textual matches, `trace_path` for callers/dependencies/data flow, and
`get_code_snippet` only after locating the exact symbol. Do not begin with
`grep`, `rg`, globbing, or ad-hoc filesystem scans while codebase-memory is
available. If `list_projects` fails, the MCP is unavailable, or no indexed
project matches, do not call the other codebase-memory tools; state that
limitation in the report and use another search method only as a documented
fallback. Never claim codebase-memory evidence that was not obtained.

The available codebase-memory tools are used as follows:

- `list_projects({})` — mandatory first step; select the indexed record whose
  `root_path` exactly equals the task's absolute `project_root`, then pass its
  returned `name` as `project`.
- `index_status({project})` — verify that the selected project is indexed and
  sufficiently fresh before relying on graph results.
- `search_graph({project, query})` — recommended natural-language/BM25
  discovery. Use `name_pattern` for exact symbol patterns, `semantic_query` as
  an array of keywords only on moderate/full indexes, and `label`,
  `file_pattern`, `relationship`, `include_connected`, `limit`, or `offset` to
  constrain results.
- `search_code({project, pattern})` — graph-enriched text matching. Choose
  `mode: compact` for signatures/metadata, `full` for source context, or
  `files` for paths; `regex`, `path_filter`, `file_pattern`, `context`, and
  `limit` refine the search.
- `trace_path({project, function_name})` — callers, callees, dependencies, or
  data flow. Use a qualified name from `search_graph`; choose `mode: calls`,
  `data_flow`, or `cross_service`, plus `direction`, `depth`, `include_tests`,
  `parameter_name`, and `risk_labels` as needed.
- `get_code_snippet({project, qualified_name})` — source for an exact symbol,
  normally after `search_graph`; `include_neighbors` adds surrounding symbols.
- `get_architecture({project, aspects})` — high-level packages, services,
  dependencies, and project structure.
- `query_graph({project, query, max_rows})` — explicit multi-hop Cypher
  analysis; use only when the simpler graph tools cannot express the question.

`index_repository`, `ingest_traces`, `manage_adr`, and `delete_project` change
indexed state or durable knowledge. They are not discovery fallbacks and may be
used only with explicit authorization and an appropriate ownership boundary.

For resumable, auditable coordination, use this plugin's `cortex-control`
skill. It records gates and delegation packages in the current repository and
never pins a profile model or replaces the parent Codex dispatch decision.

Every worker should publish one strict `cortex/report/v1` payload through
`record_report`: `summary`, `findings`, `questions`, `changed_files`, `tests`,
`evidence`, `uncertainty`, and `next_action`. After host completion or timeout,
the coordinator must finalize the corresponding attempt with
`finalize_attempt` and one of `passed`, `failed`, `blocked`, `cancelled`, or
`superseded`; every non-`passed` status requires a reason. C2/C3 evidence must
consume the attempt's one-use report receipt, and passed attempts still need
linked evidence. An invalidated running attempt may only be finalized as
`superseded` with a reason; already-terminal invalidated attempts remain
idempotent. A terminal non-success attempt may have no report, remains visible
in task status, and a gate with only terminal non-success attempts may pass
without report or evidence when acceptance permits partial failure. It may also
be carried through a closing gate when the acceptance criteria allow partial
failure; the coordinator must explicitly report that partial failure. Share
report bodies only through explicit per-attempt context grants; task listings
expose metadata. JSON records are authoritative and escaped Markdown is
generated. Use `reconcile_report_bus` after interruption because file
replacement is atomic individually, not as a multi-file transaction.
Treat every `recorded: false` or `recoverable: true` response as a control-plane
correction, not as permission to dispatch another worker. Follow its explicit
`next_action` and reuse the existing attempt whenever it names candidate
attempts. In particular, a documentation-gate evidence or receipt mismatch
must be repaired with `record_evidence`/`record_gate_outcome`; never launch a
second `technical_writer` for the same unresolved receipt. If the same
recoverable reason repeats, stop dispatching, preserve the exact error in the
current report, and route the gate to repair or handoff.
The spawn briefing supplies the canonical `attempt_id` and a lowercase stable
`submission_id`; native workers may omit either field only when the server can
infer an unambiguous active attempt, and the server then derives a deterministic
submission id. The coordinator must monitor each worker individually: wait for
host completion, poll `list_worker_questions` during every native wait interval,
inspect `get_task_status` and the report/question buses after every completion,
timeout, or newly listed question, preserve exact Cortex tool errors in the
worker report, and finalize a worker with the exact error as the reason if it
cannot report.

### User decisions and question UI

Questions belong to the main chat, even when they originate inside a hidden
worker. A worker must use `cortex.question` for a real ambiguity, branch,
approval, or missing requirement and must not silently choose among materially
different alternatives. Supplying `attempt_id` records the question on the
durable worker bus and returns `pending_user_input`; it deliberately does not
open a worker-local form. The coordinator polls `list_worker_questions`, then
calls `cortex.question` with the question's `question_id` in the main chat.
That call opens the host-native MCP elicitation UI and persists the answer back
to the worker. Options are single-select by default; `multiple: true` renders
multi-select checkboxes. Every form ends with a free-form `custom_response`
field so the user can add context, paste a path, or provide any other answer.
The coordinator forwards the exact accepted content, including structured host
attachment/image fields when present, and the worker polls
`get_worker_question_updates` before continuing. A declined, cancelled, or
unavailable form is a blocker/question outcome, not permission for the worker
to guess.

Several workers may ask questions concurrently. The question bus keeps a
global sequence plus each attempt id; the coordinator must answer open
questions independently in sequence order (or explicitly group them), never
merge answers across attempts, and continue polling until `open_count` is zero.

For round-trip reduction, use `prepare_delegation` for the status-receipt plus
delegation fast path, `prepare_delegations` for independent `parallel=true`
workers across the current executable gate wave, `complete_attempt` for host
confirmation plus an
optional strict report and terminal status, `commit_gate` for evidence plus a
gate transition, and `close_audit` for report listing plus reconciliation.
These are typed fast paths; a batch rolls back on a routing/validation failure
and returns the exact error, while the legacy tool sequences remain the
fallback for older adapters and recovery.

## Knowledge routes

For both knowledge routes, read the bundled `knowledge-harvest` and
`documentation-sync` skills completely. Their allowed documentation paths and
generated-block preservation rules are mandatory. Activate Cortex, classify
the work, initialize a task, and retain the standard gates, receipts,
reassessment, project-manifest reconciliation, and final handoff.
For a skill-row knowledge route, pass the control plane its canonical `/cortex`
activation token and the exact absolute `project_root` after route selection;
the route argument remains in the task objective and is not presented as a
separately registered host command. The server launch pattern is
`python3 /absolute/plugin/path/scripts/cortex.py` with the project supplied in
each JSON-RPC `tools/call.arguments.project_root`; do not rely on plugin cwd,
`CORTEX_PROJECT_ROOT`, `CORTEX_ROOT`, or `/tmp` fallback for a real task.

Dispatch in this order:

1. An `explorer` owns read-only repository evidence. It reports source paths,
   tests, entry points, tooling, and which allowed docs are missing or
   contradicted. It never edits.
2. A `technical_writer` owns only the justified allowed documentation paths.
   It cites the explorer evidence, preserves protected manual content, and
   reports every created, updated, and preserved file.
3. Independently verify scope, facts, generated-block boundaries, links,
   commands where safe, and the project manifest. Reconcile every changed path
   before the documentation and close gates, then create the final handoff.

### `harvest`

Perform an incremental evidence scan. Create a missing allowed document only
when source evidence justifies its required content. Update an existing
document only when its generated facts are missing, stale, or contradicted.
Do not rewrite current generated facts, unchanged files, or manual text merely
for style. The handoff must include the before/after project-manifest receipt,
the evidence for each changed doc, and an explicit list of preserved docs.

### `harvest-refresh`

Perform a full source-backed re-audit of every allowed project and discovered
feature document, even when it appears current. Replace facts only inside
`<!-- GENERATED:START -->` and `<!-- GENERATED:END -->` blocks unless verified
source evidence contradicts manual text; in that case, preserve the manual text
and report the contradiction for the user unless they explicitly authorized
editing it. Never overwrite ADRs, manually recorded gotchas, or manual feature
explanations implicitly.

Run the full verification set recorded in `docs/project/verification.md` when
safe and applicable, validate internal links and documented file paths, then
repeat the refresh planning pass. The second pass must propose no file changes;
otherwise resolve the non-idempotence before closing. The handoff must record
full-scan coverage, preservation decisions, verification evidence, the final
project-manifest receipt, and the idempotence result.
