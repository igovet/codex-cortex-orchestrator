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
- Keep the main thread focused on goals, decisions, routing, gates, integration, user communication, and final evidence. While orchestration mode is active, delegate project inspection, search, execution, testing, and editing to internal workers.
- Hidden workers remain implementation details and never become user-facing. Route every hidden-worker clarification, escalation, blocker, and handoff to the main chat; only the main agent communicates with the user. An explicitly user-authorized visible Luna task is a separate sidebar task, but still emits English-only content; the main agent localizes its findings in the primary chat.
- Choose a subagent's model and reasoning effort at dispatch time from task risk and ambiguity. Agent profiles intentionally do not pin either setting.
- Use the selected profile's exact `name` as the subagent display name and thread label; never invent or paraphrase a role name. If the creation API has no title field, preserve the exact name in the dispatch prompt and lifecycle context as the fallback identifier.
- Every delegation record and dispatch must include the exact profile, selected model, reasoning effort, ownership and allowed paths, acceptance criteria, and verification responsibilities.
- The installer owns the global `[agents] default_subagent_model` setting. For a
  configured-default Luna route, keep `expected_model` and
  `model_resolution` as durable metadata, always send `reasoning_effort`, and
  omit the native `spawn_agent.model`; use the host-reported child model for
  confirmation. Never copy `expected_model` into the native call. Explicit
  Terra/Sol/Luna overrides retain their native model field. When neither the
  configured default nor explicit Luna is available, use a hidden Terra
  fallback; never create a visible thread as a model fallback.
- Supply the explicit absolute `project_root` on every MCP call. Do not touch the project or dispatch a worker until `start_orchestration` returns `outcome="ready_to_spawn"`; fail closed for MCP failure, a mismatched/unwritable root, `CORTEX_ROOT`, or any `/tmp` fallback. One MCP process may serve multiple projects, but every task remains project-root bound.
- Parallelize read-only exploration, review, testing, and analysis. Use one writer for an overlapping code area. Use separate worktrees for independent write streams.
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

For a non-trivial repository task, read the relevant `docs/project/` and `docs/features/` material if it exists. After C2/C3 work that changes behavior, architecture, verification, conventions, or feature ownership, use `documentation-sync`.
