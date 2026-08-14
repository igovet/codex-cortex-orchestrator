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

After explicit activation, the main agent calls `cortex-control`
directly. For C2/C3 work, classify and initialize a durable task ledger before
delegating, record each delegation and gate outcome, reassess the pipeline, and
create a handoff before any pause or completion. Without activation, remain in
the normal Codex workflow and do not initialize the orchestration ledger,
create lanes, or dispatch through this policy. C1 work uses the control plane
only when explicitly activated and genuinely beneficial.

The plugin-bundled `plugins/cortex/skills/orchestrator/SKILL.md` is the single
authoritative Cortex orchestration skill source. Do not maintain or install a
second repository-level copy.
All installable agent profiles, skills, hooks, MCP configuration, and runtime
code live below `plugins/cortex/`; repository-root scripts, tests, and docs are
development-only support files.

## Working agreements

- Respond to the user in the language of their request; keep code, documentation, reports, and code comments in English unless the repository requires another language.
- Never expose secrets, credentials, private tokens, or personal data. Do not claim a check was run when it was not.
- Keep the main thread focused on goals, decisions, routing, gates, integration, user communication, and final evidence. While orchestration mode is active, delegate project inspection, search, execution, testing, and editing to internal workers.
- Workers remain hidden implementation details and never become user-facing. Route every worker clarification, escalation, blocker, and handoff to the main chat; only the main agent communicates with the user.
- Choose a subagent's model and reasoning effort at dispatch time from task risk and ambiguity. Agent profiles intentionally do not pin either setting.
- Use the selected profile's exact `name` as the subagent display name and thread label; never invent or paraphrase a role name. If the creation API has no title field, preserve the exact name in the dispatch prompt and lifecycle context as the fallback identifier.
- Every delegation record and dispatch must include the exact profile, selected model, reasoning effort, ownership and allowed paths, acceptance criteria, and verification responsibilities.
- Supply the same explicit absolute `project_root` on every MCP call. Do not touch the project or dispatch a worker until activation, classification, initialization, and status confirm `${project_root}/.codex/cortex`; fail closed for MCP failure, a mismatched/unwritable root, `CORTEX_ROOT`, or any `/tmp` fallback.
- Parallelize read-only exploration, review, testing, and analysis. Use one writer for an overlapping code area. Use separate worktrees for independent write streams.
- Before finishing a change, run the relevant non-destructive verification and state any limitation plainly.
- Treat source code, tests, and configuration as authoritative when they conflict with generated documentation.

## Project knowledge

For a non-trivial repository task, read the relevant `docs/project/` and `docs/features/` material if it exists. After C2/C3 work that changes behavior, architecture, verification, conventions, or feature ownership, use `documentation-sync`.
