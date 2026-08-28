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
- The repository-root `./cortex-dev` command is a convenience forwarding
  wrapper for `./scripts/cortex-dev`; it uses the same isolated
  `HOME=$HOME/.cortex-dev` and `CODEX_HOME=$HOME/.cortex-dev/.codex` runtime
  and must retain the same ordinary interactive Codex and no-`codex exec`
  constraints.
- Live testing is allowed only through an interactive `tmux` session running
  ordinary Codex. Create a fresh named session in the background, and deliver
  every workload command directly with `tmux send-keys`; do not open or drive a
  foreground console, use `codex exec`, an exec-mode wrapper, or a
  detached/non-interactive Codex substitute. Use the current user's default
  tmux server so the named smoke session is visible to `tmux ls`; `tmux` uses
  `$TMUX` to locate that server when commands are issued from an existing tmux
  pane. If the default server or nested-server path is denied with
  `Operation not permitted`, classify the smoke as failed or unverified and
  report that terminal limitation; do not silently switch to an independent
  socket. Use this session sequence,
  replacing `<repository-root>` with the exact checkout:

  ```bash
  session_name=cortex-v12-smoke
  tmux_cmd=(tmux -f /dev/null)
  "${tmux_cmd[@]}" has-session -t "=$session_name" 2>/dev/null && \
    "${tmux_cmd[@]}" kill-session -t "=$session_name" || true
  "${tmux_cmd[@]}" new-session -d -s "$session_name" -c "<repository-root>" bash
  "${tmux_cmd[@]}" send-keys -t "=$session_name:0.0" 'cd <repository-root> && ./scripts/cortex-dev; status=$?; printf "Cortex live-dev exit=%s\\n" "$status"' C-m
  # Phase 1 ends here: return control to the coordinator while Codex starts.
  # In phase 2, after a coordinator poll confirms the prompt is usable, inject
  # only the narrow smoke input into the already-open session as a separate
  # later action (do not execute this line in the phase-1 launcher batch):
  "${tmux_cmd[@]}" send-keys -t "=$session_name:0.0" '<targeted test input>' C-m
  # Phase 3: give each fresh bounded snapshot back to the coordinator/LLM for
  # interpretation and a decision to poll again, continue, or clean up.
  "${tmux_cmd[@]}" capture-pane -p -t "=$session_name:0.0" -S -200 -E -1
  "${tmux_cmd[@]}" kill-session -t "=$session_name" 2>/dev/null || true
  ```

  If the bounded `capture-pane` result does not contain the launcher's target
  and an interactive Codex prompt, or the Codex TUI redraws it away before the
  result can be captured, use this output-only capture fallback. Start it
  before sending the launcher; it records the PTY stream while Codex remains an
  ordinary interactive process. This is not a shell pipe into Codex and must
  not be used to feed input, replace `send-keys`, or run an exec-mode command.
  The coordinator owns the three phases: launch and return; later send the
  user message after a poll confirms a usable prompt; then repeatedly read a
  bounded snapshot and give it to the coordinator/LLM for interpretation.

  ```bash
  capture_dir="$(mktemp -d "${TMPDIR:-/tmp}/cortex-v12-smoke.XXXXXX")"
  capture_path="${capture_dir}/pane.raw"
  (umask 077; : >"${capture_path}")
  "${tmux_cmd[@]}" pipe-pane -t "=$session_name:0.0" "cat > '${capture_path}'"
  # Phase 1: launch ordinary interactive Codex and return immediately.
  "${tmux_cmd[@]}" send-keys -t "=$session_name:0.0" 'cd <repository-root> && ./scripts/cortex-dev; status=$?; printf "Cortex live-dev exit=%s\\n" "$status"' C-m
  # Do not put a readiness/result wait loop here. The coordinator performs
  # separate bounded reads and sends the phase-2 input only after interpreting
  # a usable prompt. Phase 3 repeats bounded reads while the session is alive;
  # each snapshot is fed back to the coordinator/LLM, which decides whether to
  # poll again, continue, or clean up. Unchanged output is not completion.
  tail -c 20000 "${capture_path}"
  "${tmux_cmd[@]}" pipe-pane -t "=$session_name:0.0"
  "${tmux_cmd[@]}" kill-session -t "=$session_name" 2>/dev/null || true
  rm -f -- "${capture_path}"
  rmdir -- "${capture_dir}"
  ```

  The `pipe-pane` command is a fallback for TUI capture loss, not a readiness
  signal by itself. A coordinator poll interprets the configured-MCP line and
  the `configured Cortex MCP default_tools_approval_mode=approve` line plus
  the interactive prompt before sending input; there is no launcher-side
  readiness wait. The
  20,000-byte tail is the only retained evidence. Keep the temporary directory
  owner-only, remove it after inspection, and never paste raw output that
  contains user prompts, tokens, or personal data.

  The first tmux management commands run from the controlling shell; the
  launcher and smoke commands themselves must be injected into the named pane.
  `-f /dev/null` prevents an unrelated tmux configuration from changing the
  smoke while retaining the user's default tmux server. Use bounded
  `capture-pane` output as
  the result record. If the default socket is denied, do not fall back to a
  foreground console, independent socket, `codex exec`, or a shell pipe:
  classify the smoke as failed or unverified and report the host's
  terminal/permission limitation; do not fall back to an independent socket.
  If the session exits early, the launcher
  prints an error, the exit marker is non-zero, or the bounded capture has no
  usable result, report the live-dev test as failed or unverified from the
  capture; never infer success. Before the scoped smoke, record the candidate
  target printed by the launcher as `$HOME/.cortex-dev/.codex` and the
  refreshed cache version shown by the launcher/synchronization output. Keep
  each test narrowly targeted to the modified tool, function, or contract; do
  not turn a focused smoke into a broad repository or release run. Record the
  exact session and socket commands, isolated target, scope, outcome, cleanup,
  and any unrun checks. A successful smoke must end with the exact named
  session gone. Do not kill the user's default tmux server during cleanup.
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
