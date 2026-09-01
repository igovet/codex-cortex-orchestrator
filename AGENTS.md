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
- MCP argument contracts belong exclusively to each tool's advertised input
  schema and tool/property descriptions. Never put MCP parameter names,
  request shapes, required/optional field lists, enums, validation limits, or
  sample tool payloads in skills, agent prompts, live-dev workload prompts, or
  other model instructions. Those instructions may state the task-specific
  behavior and semantic outcome to exercise, but the model must derive every
  call argument from the live advertised tool contract. A tool call that needs
  parameter hints outside that contract is a schema/description defect; fix
  the tool contract and add a first-call regression test instead of teaching
  the call shape in a skill or prompt.
- For source-only package checks, use `./scripts/sync-cortex.sh --check` or
  `./scripts/sync-cortex.sh --dry-run`; these are read-only validation only.
  Do not invoke cachebuster helpers manually, run `codex plugin add`, or edit
  marketplace or Codex configuration as a substitute. Never run normal
  `sync-cortex.sh` against the user's stable `HOME` or `CODEX_HOME`.
- Follow semantic versioning in
  `plugins/cortex/.codex-plugin/plugin.json`: patch for fixes, minor for
  backward-compatible features, and major for large or breaking changes. Do
  not change unrelated version components.
- After any edit to the installable payload below `plugins/cortex/`, update the
  release semantic version when required and regenerate the content-addressed
  `+codex.sha256.<digest-prefix>` cache stamp from the complete current plugin
  payload before running tests, source/package validators, sync checks, or
  live-dev. Never run or report a release-sensitive check against a manifest
  whose cache suffix is known to be stale. If any plugin payload file changes
  after stamping, the prior stamp and every later release-sensitive test result
  are invalid: regenerate the cache stamp first, then rerun the affected checks.
  Updating this repository stamp never authorizes installing or modifying the
  user's stable Cortex plugin.
- Do not run release-sensitive test suites concurrently in the same checkout.
  Some sync regressions deliberately create and clean temporary bytecode or
  candidate state below the source plugin tree; a concurrent validator can
  observe that bounded fixture and falsely report cache-stamp drift. Parallelize
  independent read-only work or use isolated worktrees, but run the authoritative
  package/sync/marketplace suite sequentially per checkout.
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
- Live-dev verification is an operator-controlled ordinary Codex session. `./scripts/cortex-dev` refreshes only the isolated `HOME=$HOME/.cortex-dev` and `CODEX_HOME=$HOME/.cortex-dev/.codex` candidate; it does not create tmux. Use `./scripts/cortex-live-smoke start` to create the exact `cortex-v12-smoke` session on the current user's default tmux server. The helper first creates an ordinary `bash` pane, attaches an output-only `tmux pipe-pane` observer to that exact pane, then inserts the fixed launcher command literally and submits it with one standalone Enter. The bounded temporary stream and its metadata are owner-only, refer only to that named session, and are removed by `stop`; the pipe never receives input. The launcher prints `Cortex live-dev exit=<status>` and exits with that same status. Never use `codex exec`, nested tmux, an alternate socket, or the stable plugin environment.
- Observe the real session with `tmux ls`, `./scripts/cortex-live-smoke status`, `./scripts/cortex-live-smoke capture`, `./scripts/cortex-live-smoke events`, or `TERM=xterm-256color tmux -f /dev/null attach -t cortex-v12-smoke`. `capture` reads the bounded output-only stream so an alternate-screen trust or composer redraw remains observable when `capture-pane` is stale. `events` reads the exact session's bounded owner-only sanitized MCP observation stream; it is observation-only and never judges readiness, errors, replay, or acceptance. After `start`, visibly confirm the Codex state before any workload submission. `pane_current_command=codex` alone is insufficient: an early send during TUI initialization can lose both text submission and Enter. If the visibly observed fresh-project trust screen requests one acknowledgement, the operator/LLM may run `./scripts/cortex-live-smoke enter` exactly once only after that observation; it sends one standalone Enter to the exact pane and never auto-trusts a directory or changes Codex trust configuration. Then visibly confirm that the interactive composer is rendered before sending a task prompt. Before workload submission, require a passive host-owned activation receipt proving agreement between the exact isolated candidate, registered Cortex server, and advertised catalogue identity; the transport exposes it, while the LLM/coordinator verifies it, and absence means unverified environment. Every workload must select the real `$cortex:orchestrator` skill token (or an actual host skill-picker selection), never decorative bracket text such as `[$cortex:orchestrator]`; activated skill content is normally host-supplied, while compaction/reset recovery may reload the exact installed skill through the host loader or sandboxed read-only access without user approval or elevated execution and never through an MCP resource or project copy. Once `cortex:orchestrator` is selected, the first project execution action must be `open_task`; prose activation acknowledgement, shell/repository inspection, project-state checks, or worker dispatch before it is a route violation. Every task authors its own task-specific prompt based on its changed behavior; the stabilization fixture is only an example. The prompt must say the session is already live-dev and prohibit nested tmux, cortex-dev, shell validation, and repository inspection.
- Submit with `./scripts/cortex-live-smoke send --prompt-file FILE`. After the operator has visibly confirmed the composer, the transport normalizes the UTF-8 prompt to one line, inserts the complete prompt literally with one `send-keys -l` delivery, waits a real five seconds after insertion returns, and sends exactly one standalone named `Enter` to the same exact pane. It sends no pre-submit `C-m` or `C-j`. Its receipt reports insertion, the five-second wait, and one key delivery only; it never claims that the TUI accepted or submitted the prompt. The transport does not poll readiness or decide acceptance. The coordinator/LLM must confirm TUI acceptance and task progress from the real pane and bounded events. Observe actual task-relevant Cortex MCP calls and results. Any `Cortex tool error`, `validation_error`, `schema_unsupported`, traceback, missing success marker, or repeated successful mutation without an explicitly ambiguous prior transport result is a failed live check; backend idempotency does not excuse an unexplained replay. For the stabilization fixture, accept its sentinel only after exactly one task-creation request has produced a non-replayed success. Cleanup never changes that outcome.
- For every native worker spawned by live orchestration, the LLM verifier must inspect a bounded sanitized structured event stream as well as the coordinator pane because worker MCP calls/errors may be hidden. The helper may expose events but must not decide pass/fail. Acceptance requires a clean first worker-owned report-submission success, zero prior hidden validation/tool errors or mutation replays; a final report reference alone is insufficient.
- A fresh native worker must begin by consuming the server-owned assignment evidence using the opaque assignment anchor from the exact server-rendered dispatch brief. The coordinator/host must deliver that renderer output byte-for-byte; it must not reconstruct or paraphrase it. A task-state read before evidence consumption is a failed worker bootstrap, even if it later succeeds. The evidence result is the worker's bootstrap authority for subsequent task-scoped publication.
- Before cleanup, capture the explicit `Cortex live-dev exit=0` marker when applicable. Then run `./scripts/cortex-live-smoke stop` (or `--interrupt` after failure), which targets and removes only the exact named session; never kill the tmux server. Record the session, isolated target, observed tool result/error, scope, outcome, and unrun checks.
- The focused E2E acceptance scenario is multi-turn and LLM-driven: in a separate test project, observe the live pane, answer exactly one product clarification with the predefined safe answer, later approve the visibly rendered plan, and continue planner → implementation → independent verification → documentation-impact assessment → closure. Inspect every native worker's bounded structured event stream, including the first report-submission event; any hidden tool error or unexplained replay fails the scenario. The transport only delivers text/keys and exposes observations; it never answers clarification, approves a plan, or decides acceptance autonomously.
- `cortex-live-smoke` is transport-only: it never parses readiness, trust, rollout, sentinels, acceptance, approvals, MCP errors, or retry conditions. The coordinator/LLM reads the real attached or bounded captured terminal and owns every decision. Prompt delivery is one literal normalized insertion followed by one standalone `Enter` key; the separate `enter` action is only an explicit transport key after a visibly observed trust screen.
- The transport key is the tmux named key `Enter` (not a second key or a control-key alias): after one literal prompt insertion it waits exactly five seconds and delivers exactly one `Enter`.
- Before finishing a change, run the smallest non-destructive check set that
  proves the affected behavior, then broaden validation in proportion to risk.
  State every unrun release gate or environmental limitation plainly.
- `./scripts/cortex-live-smoke start --workdir PATH` may select a separate existing canonical test-project directory for Codex's cwd; the fixed launcher and candidate refresh always use this checkout's absolute `scripts/cortex-dev`.
- To verify process-level continuation of the same ordinary interactive Codex thread, first stop only the exact smoke session, then use `./scripts/cortex-live-smoke start --workdir PATH --resume-last`. This refreshes and verifies the isolated candidate again before invoking ordinary `codex resume --last` in the same canonical cwd. Confirm the resumed transcript and existing Cortex task reference before sending continuation text; a new `open_task` is a failed resume check. Never use this flag for the first run or with a different workdir.
- `scripts/cortex-dev` records its caller cwd, temporarily enters this checkout only for candidate refresh/sync, then restores the caller cwd before `exec codex`; therefore `--workdir PATH` is also the task project root seen by ordinary Codex.
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
