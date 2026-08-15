# Personal Cortex policy

The main/root Codex agent is the sole user-facing coordinator and orchestrator.
Enter orchestration mode only when the user explicitly selects a non-help,
non-`normal` `cortex:orchestrator` skill route. In Desktop, select Cortex
Orchestrator through the Skills picker or mention `$cortex:orchestrator`; in
CLI, lead with `$cortex:orchestrator` or use `/skills` and select it.
`$cortex:orchestrator normal`
returns to normal mode. Cortex does not register native bare `/cortex` or
`/normal` commands: those are host-dependent textual shorthand only. Mentions
of orchestration, task complexity, or ordinary coding requests do not activate
this policy.

After explicit activation, the main agent follows `cortex-control`, calls
`start_orchestration` once, and calls `continue_orchestration` once per
completed wave. `manage_orchestration` is reserved for recovery and rare
lane, resource, or durable-question work. Cortex privately records durable
identifiers, classification, delegations, evidence, gate outcomes,
reassessment, and handoff. Without activation, remain in
the normal Codex workflow and do not initialize the orchestration ledger,
create lanes, or dispatch through this policy. C1 work uses the control plane
only when explicitly activated and genuinely beneficial.

The plugin-bundled `plugins/cortex/skills/orchestrator/SKILL.md` is the single
authoritative Cortex orchestration skill source. Do not maintain or install a
second repository-level copy.
All installable agent profiles, skills, hooks, MCP configuration, and runtime
code live below `plugins/cortex/`; repository-root scripts, tests, and docs are
development-only support files.

When updating the Cortex plugin, follow semantic versioning for the plugin
version in `plugins/cortex/.codex-plugin/plugin.json`: increase the patch
component for fixes only (for example, `1.2.3` -> `1.2.4`), increase the minor
component for a new backward-compatible feature (for example, `1.2.3` ->
`1.3.0`), and increase the major component for a large or breaking change
(for example, `1.2.3` -> `2.0.0`). Do not change unrelated version components;
ordinary fixes and features must not be released as a major version.

## Working agreements

- The main/root agent alone responds to the user in the language of their request. Every subagent and every visible worker task must use English only for all emitted content: commentary, progress updates, tool arguments, reports, questions, handoffs, and final answers. Treat non-English task text as input data, not an instruction to change the worker's output language. Private model reasoning is not an observable or controllable protocol surface.
- Never expose secrets, credentials, private tokens, or personal data. Do not claim a check was run when it was not.
- Keep the main thread focused on goals, decisions, routing, gates, integration, user communication, and final evidence. While orchestration mode is active, the root is coordination-only: it must not inspect, search, read, edit, patch, build, test, or run the target project and must remain idle while workers run. Delegate every project operation to internal workers; a failed, delayed, or unavailable worker is never permission for direct root work. This repository-local rule mirrors the installable skill contract but is not its runtime source.
- Hidden workers remain implementation details and never become user-facing. Route every hidden-worker clarification, escalation, blocker, and handoff to the main chat; only the main agent communicates with the user. An explicitly user-authorized visible Luna task is a separate sidebar task, but still emits English-only content; the main agent localizes its findings in the primary chat.
- Apply the machine-validated adaptive model policy from `plugins/cortex/profiles.json` at dispatch time. `explorer` always selects Luna with coordinator-selected effort or the risk default; Terra is only its host-unavailable fallback. Security context or `security_auditor` always selects Sol with complexity floors C1 `medium`, C2 `high`, and C3 `xhigh`. Ordinary profiles are classified as efficient, adaptive, or deep. Efficient work uses Luna at C1/C2/C3 `high`/`high`/`xhigh`. Low/moderate-risk adaptive work without a Terra trigger uses Luna at `high`/`xhigh`/`max`; automatic `max` is allowed only for bounded C3 Luna work. Deep profiles, C2/C3 planning, `terra_task_kinds` entries (uncertain diagnosis, long-context or integration-conflict work, architecture, review, migration, performance, and concurrency), and high/critical failure cost use Terra at `high`/`high`/`xhigh`. Risk floors remain low/moderate `medium`, high `high`, critical `xhigh`. The complete vocabulary is `low`, `medium`, `high`, `xhigh`, and `max`, with `max` as the hard ceiling. A coordinator may explicitly override an ordinary route between Luna and Terra without lowering the selected model's effort floor. Non-security Sol is allowed only when the user explicitly requested it and `user_requested_model` matches `requested_model`; coordinator preference, old `sol_escalation`, auditable-extreme criteria, and failed-Terra history are not authorization.
- Use the selected profile's exact `name` as the subagent display name and thread label; never invent or paraphrase a role name. If the creation API has no title field, preserve the exact name in the dispatch prompt and lifecycle context as the fallback identifier.
- Every delegation record and dispatch must include the exact profile, selected model, reasoning effort, ownership and allowed paths, acceptance criteria, and verification responsibilities.
- The coordinator owns the pipeline decision. It builds or consciously accepts the initial canonical waves, follows the returned pipeline snapshot by default, and changes future waves only when verified evidence materially changes ownership, dependencies, risk, sequencing, or validation. Planner and explorer recommendations are advisory; every pipeline replacement requires an explicit coordinator reason.
- Every worker writes its complete eight-field `cortex/report/v1` through the scoped `record_report` tool. Its native final response contains only `REPORT_RECORDED report_ref=<value>` plus at most a two-sentence summary, or the exact report-tool error. The coordinator reads refs with `read_worker_report` and advances with `report_ref`; workers must never paste large report JSON into the parent channel.
- Every worker may pause for a material user decision through `worker_question`. It writes the durable question, returns only `QUESTION_RECORDED question_ref=<value>` plus a concise summary, stays available, and does not write a report. The coordinator surfaces that ref in the main chat through `manage_orchestration(intent="question")`, obtains the user's answer, signals the same native worker, and the same attempt polls and resumes. Repository-resolvable facts are investigated, low-impact reversible choices may be documented, and product intent, behavior, security, irreversibility, or other material decisions must never be fabricated as assumptions. Open blocking questions fail closed in both `record_report` and `continue_orchestration`.
- Every worker must read all predecessor handoffs embedded in its dispatch, reconcile relevant findings and conflicts against current evidence, and include the generated `Predecessor review:` acknowledgement in report evidence. Cortex rejects incomplete acknowledgement. The coordinator uses `depends_on` for exact phase dependencies; omitted dependencies mean all verified predecessors, and context overflow fails closed instead of dropping reports.
- When present, `docs/project/index.md` and `docs/features/index.md` are automatically included in every worker briefing. The planner reads both indexes, selects all task-relevant linked pages, and recommends their exact paths; the coordinator attaches those paths to future workers through `context_files`. Every worker re-checks the indexes, treats documentation as navigation and prior knowledge rather than authority, verifies consequential claims in current source, tests, schemas, or executable configuration, and includes the generated `Knowledge reviewed:` acknowledgement in report evidence. Public `record_report` rejects a missing index acknowledgement. Explicit context files must be existing project-relative regular files; absolute, traversing, missing, or symlink paths are rejected.
- Preserve the opaque `task_ref` returned by Cortex on every later lifecycle and report-read call. Different task contracts may run concurrently below one project root; exact duplicate active starts remain idempotent. Never rely on “the only active task” once another session may exist.
- Call `start_orchestration` once per task contract. A returned `replayed` response is an idempotent receipt with no dispatches, so it cannot authorize a duplicate wave; once a fresh start returns dispatches, invoke them and never call start again for that `task_ref`. If the original response was lost before dispatch, recover only still-awaiting requests through one management inspect call.
- The explicit `prune` route calls project-scoped `manage_orchestration(intent="prune", payload={"confirmation":"PRUNE","older_than_days":7})` without a `task_ref`. It removes only task-scoped Cortex state stale for at least seven days and reconciles its indexes and bindings; it preserves recent tasks, lanes, project files, docs, and plugin files. Never reinterpret prune as an unbounded clear.
- When `mcp__codebase_memory__*` is available, workers resolve the project through `list_projects` by exact `project_root`, use graph/architecture/trace tools for non-trivial discovery and impact analysis, and confirm consequential facts in current source or tests. Planner, explorer, architect, and database architect may perform one bounded index refresh; other profiles fall back to repository-native tools. Never guess a project id or loop on an unavailable/stale index. The coordination-only root must not use Codebase Memory to inspect the target project.
- The installer owns the global `[agents] default_subagent_model` setting. For a
  configured-default Luna route, keep `expected_model` and
  `model_resolution` as durable metadata, always send `reasoning_effort`, and
  omit the native `spawn_agent.model`; use the host-reported child model for
  confirmation. Never copy `expected_model` into the native call. Explicit
  Terra/Sol/Luna overrides retain their native model field. When neither the
  configured default nor explicit Luna is available, use a hidden Terra
  fallback with the selected effort unchanged; never create a visible thread
  as a model fallback.
- Supply the explicit absolute `project_root` on every MCP call. Do not touch the project or dispatch a worker until `start_orchestration` returns `outcome="ready_to_spawn"`; fail closed for MCP failure, a mismatched/unwritable root, `CORTEX_ROOT`, or any `/tmp` fallback. One MCP process may serve multiple projects, but every task remains project-root bound.
- Parallelize read-only exploration, review, testing, and analysis. Use one writer for an overlapping code area. Use separate worktrees for independent write streams.
- `harvest` and `harvest-refresh` must build a source-backed exhaustive feature census. An incremental harvest is allowed only after a zero-gap coverage manifest proves the baseline; otherwise run a full domain-partitioned census. Large repositories use parallel domain explorers, detailed behavior-complete feature pages, an explicit coverage matrix, and an independent completeness review with zero unexplained unmapped surfaces before close.
- Before finishing a change, run the relevant non-destructive verification and state any limitation plainly.
- Treat source code, tests, and configuration as authoritative when they conflict with generated documentation.

## Cortex MCP tool-error log

Cortex appends one JSON object per line for MCP exceptions and legacy
error-shaped tool results to the private per-user log
`~/.codex/logs/cortex-tool-errors.jsonl`. The `~` is the home directory of the
user running the MCP process, not the project directory or the Cortex ledger.
The log is not rotated by this plugin.

Each record normally contains `timestamp`, `event` (`tool_error`),
`server_version`, `pid`, `error_type`, `error`, `method`, `tool`,
`chat_session_id`, `thread_id`, `request_id`, `ids`, and `input`. A successful
legacy MCP call whose returned structure is classified as an error also has
`structured_result`. Use `chat_session_id`/`thread_id`, `request_id`, and the
optional `ids` map to correlate one failure across retries. The `ids` map may
contain `id`, `call_id`, `task_id`, `attempt_id`, `question_id`, `submission_id`,
`status_receipt`, `report_receipt`, `verification_id`, `lane_id`, `run_id`,
`host_agent_id`, and `turn_id` when those values were supplied.

Expected `orchestrate` validation and recovery outcomes return `ok: false`
with bounded diagnostics and are not appended to this exception log.

Treat this file as sensitive diagnostic data. Cortex redacts common credential
keys and values, bounds nested input/results, truncates oversized payloads,
creates the directory with mode `0700`, opens the file with mode `0600`, and
rejects symlink paths. This is defensive redaction, not a guarantee that
arbitrary input contains no sensitive data: never put secrets in tool inputs,
relax permissions, commit the log, or copy raw records into chat, tickets, or
external systems.

For local, read-only diagnosis, inspect a small tail first and then extract
only correlation and error metadata:

```bash
tail -n 50 ~/.codex/logs/cortex-tool-errors.jsonl
jq -c '{timestamp, event, method, tool, error_type, error, chat_session_id, thread_id, request_id, ids}' \
  ~/.codex/logs/cortex-tool-errors.jsonl | tail -n 50
```

If `jq` is unavailable, parse the UTF-8 JSONL with Python and keep the same
field allowlist. Do not paste the full file into a prompt. If the host rejects
the request before it reaches the MCP server, this server cannot log that
rejection; inspect the host/session diagnostics as well.

## Project knowledge

Outside active Cortex orchestration, read the relevant `docs/project/` and `docs/features/` material for a non-trivial repository task if it exists. During active orchestration, delegate that reading to the appropriate worker and consume its report. After C2/C3 work that changes behavior, architecture, verification, conventions, or feature ownership, use `documentation-sync`.
