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
- For source-only package checks, use `./scripts/sync-cortex.sh --check` or
  `./scripts/sync-cortex.sh --dry-run`; these are read-only validation only.
  Do not invoke cachebuster helpers manually, run `codex plugin add`, or edit
  marketplace or Codex configuration as a substitute. Never run normal
  `sync-cortex.sh` against the user's stable `HOME` or `CODEX_HOME`.
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
- Every live-dev test must first refresh the candidate cache/version through
  `./scripts/cortex-dev`; it is the only supported live-dev entry point. It
  creates or reuses the exact isolated `$HOME/.cortex-dev` candidate, exports
  `HOME=$HOME/.cortex-dev` and `CODEX_HOME=$HOME/.cortex-dev/.codex`, then runs
  the repository-supported `sync-cortex.sh` only in that isolated environment
  before launching ordinary Codex. Never install, reinstall, update, or
  synchronize the user's real installed plugin; do not use `sync-cortex.sh`
  directly as a live-dev mechanism.
- Live testing is allowed only through an interactive `tmux` session running
  ordinary Codex. Create a fresh named session in the background, and deliver
  every workload command directly with `tmux send-keys`; do not open or drive a
  foreground console, use `codex exec`, an exec-mode wrapper, or a
  detached/non-interactive Codex substitute. Use this bounded session
  sequence, replacing `<repository-root>` with the exact checkout:

  ```bash
  session_name=cortex-v12-smoke
  tmux has-session -t "$session_name" 2>/dev/null && tmux kill-session -t "$session_name" || true
  tmux new-session -d -s "$session_name" -c "<repository-root>" bash
  tmux send-keys -t "$session_name" 'cd <repository-root> && ./scripts/cortex-dev; status=$?; printf "Cortex live-dev exit=%s\\n" "$status"' C-m
  # After the launcher is ready, inject only the narrow smoke input:
  tmux send-keys -t "$session_name" '<targeted test input>' C-m
  tmux capture-pane -p -t "$session_name" -S -200 -E -1
  tmux kill-session -t "$session_name" 2>/dev/null || true
  ```

  The first `tmux` management commands run from the controlling shell; the
  launch and smoke commands themselves must be injected into the named
  session. Use bounded `capture-pane` output as the result record. If the
  session exits early, the launcher prints an error, the exit marker is
  non-zero, or the bounded capture has no usable result, report the live-dev
  test as failed or unverified with the captured outcome; never infer success.
  Before the scoped smoke, record the candidate target printed by the launcher
  as `$HOME/.cortex-dev/.codex` and the refreshed cache version shown by the
  launcher/synchronization output. Keep each test narrowly targeted to the
  modified tool, function, or contract; do not turn a focused smoke into a
  broad repository or release run. Record the exact session command, isolated
  target, scope, outcome, cleanup, and any unrun checks.
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

- After every completed task, use the required focused live-dev test to refresh
  only the repository candidate cache/version through `./scripts/cortex-dev`.
  This applies even when the task changes documentation only. The refresh must
  target the isolated `.cortex-dev/.codex` runtime; it must never install,
  reinstall, synchronize, or otherwise update the user's Cortex plugin.
- After every completed task, run a small, targeted live-dev test that
  exercises the user's completed change. Use the background interactive
  `tmux`/ordinary `codex` sequence above, including direct command injection,
  bounded capture, explicit cleanup, and failure/unverified reporting; keep
  the scope limited to the changed behavior, and record the exact command,
  outcome, and any unrun checks.

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
