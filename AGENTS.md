# Cortex repository development policy

This file governs work in this source checkout only. It is not an installed or
global Cortex orchestration contract. Runtime behavior for every installed
project must be fully specified below `plugins/cortex/`, primarily in
`plugins/cortex/skills/orchestrator/SKILL.md`,
`plugins/cortex/skills/cortex-control/SKILL.md`, the bundled supporting skills,
agent profiles, hooks, schemas, and runtime code. Tests must prove important
runtime guarantees from those bundled sources without depending on this file.

## Local workflow

- Do not activate Cortex merely because a task is complex or mentions
  orchestration. Use the normal Codex workflow unless the user explicitly
  selects a non-help, non-`normal` `cortex:orchestrator` route. After explicit
  selection, follow the bundled `orchestrator` and `cortex-control` skills; do
  not recreate their runtime protocol here.
- The plugin-bundled orchestrator skill is the single authoritative skill
  source. Do not add or install a second repository-level copy.
- All installable profiles, skills, hooks, MCP configuration, and runtime code
  live below `plugins/cortex/`. Repository-root scripts, tests, and documents
  are development support only. When runtime behavior changes, update the
  bundled contract and add a parity or behavior test before trimming any local
  development note.
- During Cortex development, install or update the plugin only with
  `./scripts/sync-cortex.sh`; use `./scripts/sync-cortex.sh --check` or
  `./scripts/sync-cortex.sh --dry-run` for read-only validation. Do not invoke
  cachebuster helpers manually, run `codex plugin add`, or edit marketplace or
  Codex configuration as a substitute.
- Follow semantic versioning in
  `plugins/cortex/.codex-plugin/plugin.json`: patch for fixes, minor for
  backward-compatible features, and major for large or breaking changes. Do
  not change unrelated version components.
- Source-mode validation may point the MCP server at this checkout in an
  isolated temporary project. This is source evidence, not installed-plugin
  verification. Never install, reinstall, or update the user's Cortex plugin
  without explicit user direction.
- Never expose secrets, credentials, private tokens, personal data, worker
  reports, or raw diagnostic logs. Do not claim a check was run when it was
  not. Follow `SECURITY.md` and the bundled Cortex content-safety/runtime
  contracts for sensitive data.
- Live testing is allowed only through an interactive `tmux` session running
  ordinary Codex (`tmux ...` then `codex`). Do not run live tests with
  `codex exec`, an exec-mode wrapper, or a detached/non-interactive substitute.
  Keep each live test narrowly targeted to the modified tool, function, or
  contract; do not turn a focused smoke into a broad repository or release
  run. Record the exact session command, scope, outcome, and any unrun checks.
- Before finishing a change, run the smallest non-destructive check set that
  proves the affected behavior, then broaden validation in proportion to risk.
  State every unrun release gate or environmental limitation plainly.
- After editing behavior, interfaces, commands, diagrams, or release metadata,
  re-read `README.md`, `SECURITY.md`, and every affected Markdown file. Check
  links, Mermaid diagrams, version strings, examples, and documented commands
  against current source and tests; documentation drift is a release defect.
- Treat source code, tests, schemas, and executable configuration as
  authoritative when they conflict with generated documentation.
- Legacy compatibility must never be added or retained unless the user
  explicitly directs it.

## Post-task requirements

- After every completed task, update only the repository's cache version in the
  plugin settings through the supported `./scripts/sync-cortex.sh` workflow.
  This requirement applies even when the task changes documentation only. For
  this cache-version action, never install, reinstall, synchronize, or otherwise
  update the user's Cortex plugin; any such plugin action requires explicit
  user direction.
- After every completed task, run a small, targeted live-dev test that
  exercises the user's completed change. Use the interactive `tmux`/ordinary
  `codex` workflow required above, keep the scope limited to the changed
  behavior, and record the exact command, outcome, and any unrun checks.

## Local diagnostics and project knowledge

- The MCP tool-error log is private per-user data, not repository state. For
  source diagnosis, follow the safe, bounded procedure in the bundled
  `cortex-control` skill and `SECURITY.md`; never commit the log or paste raw
  records into a prompt, issue, or chat.
- Outside active Cortex orchestration, read relevant `docs/project/` and
  `docs/features/` pages for non-trivial repository work. During explicitly
  activated orchestration, the bundled skills own project-knowledge routing.
  After C2/C3 changes to behavior, architecture, verification, conventions, or
  feature ownership, use `documentation-sync`.
