# Cortex repository development

Use `python3`. Runtime behavior belongs entirely below `plugins/cortex/`.
Do not activate orchestration merely because this repository is named Cortex;
only an explicit user selection activates the bundled orchestrator skill.

The server stores tasks, advisory governance and Markdown reports. The model
owns the pipeline, native subagents, steering, evidence interpretation and
completion. Keep tool argument contracts in the advertised schemas and property
descriptions only, never in skills or profile instructions. Preserve all 22
specialist profiles and their shared report protocol. Do not introduce
compatibility routes, mandatory stages or approval machines. Lifecycle hooks may
perform short local source-memory, observation and exact registered-file integrity
work; they never choose agents or decide task acceptance.

Use one real `pipeline.md` per task, newest edition first with older editions
below. Other reports are immutable real Markdown files. Store report bodies in
`.codex/cortex/<task>/` in the project and only metadata/relations in SQLite.
Both the newest-first catalogue and report content use bounded cursor reads.
Workers select relevant reports rather than reading everything.

Read relevant docs/project and docs/features pages before nontrivial work.
After behavior changes update README.md, SECURITY.md and affected documentation;
check links and commands against source. Keep secrets, private reports and raw
host logs out of repository documents and diagnostics. Report unrun checks.

For this replacement preserve semantic version 1.15.6. After any plugin payload
edit, regenerate its complete content hash with
`python3 -B scripts/cortex_package.py stamp` before release-sensitive checks.
Run package, sync and test checks sequentially in the same checkout. Use the
smallest meaningful tests first. Source-only sync uses `--check` or `--dry-run`.

Never install or update the stable user's Cortex plugin. Live preparation must
use `./scripts/cortex-dev` (or the root forwarding wrapper), which alone prepares
and installs the exact isolated `$HOME/.cortex-dev/.codex` candidate. No direct
normal sync, ad hoc cachebuster or stable configuration changes are allowed.

All live-dev tests use `gpt-5.6-luna` with `high` effort for the coordinator.
Native test workers also use Luna, at medium or high effort; heavy models are
prohibited in live CLI/Desktop tests. Keep this test policy in isolated launcher
configuration, without changing stable user settings or general plugin routing.

After a completed change run a focused ordinary interactive Codex live scenario.
Use `./scripts/cortex-live-smoke start --workdir PATH` for the exact
`cortex-markdown-smoke` session on the default tmux server. The helper creates bash,
attaches an owner-only output pipe, and enters the launcher literally. Observe
`capture` and `status`; visibly confirm any trust prompt before the one explicit
`enter`, and confirm the composer before submitting work. Check the passive MCP
initialization receipt against the isolated candidate and seven-tool catalogue.
Submit with `send --prompt-file FILE`: one literal insertion, five real seconds,
then one named Enter. The transport never decides readiness or acceptance.
Workloads begin with the actual `$cortex:orchestrator` token; remaining text is
ordinary product work without orchestration test instructions.

Use `events` to inspect bounded metadata-only MCP outcomes for coordinator and
native workers, and `calls` to inspect every host-tool invocation and observed
result. A command wrapper must expose its exit code or running session receipt; stdout
alone is unverified. Never accept hidden errors, truncation or unexplained duplicate writes as a
clean run. When rejecting a live run, capture the complete current `calls` and
`events` tails and run `audit` before stopping it; do not stop monitoring after the
first discovered fault. The coordinator owns every user steering and acceptance decision.
For resume stop only that session, then start with the same workdir and
`--resume-last`; confirm the existing task before continuing. No `codex exec`,
nested tmux, alternate sockets or stable plugin environment. Capture the exit
marker and use `stop` (or `stop --interrupt` after failure) to remove the exact
session and temporary observation streams. Never kill the tmux server.

Real Desktop uses `scripts/cortex-desktop-dev`, the same isolated candidate and
a disposable Electron profile. Start with `--prompt-file`, confirm the prepared
composer, and use its `send` command, which focuses that exact window, submits with
`Ctrl+Enter`, and succeeds only after one new task receipt appears. Use `events` for MCP outcomes and `calls` for every
argument-free coordinator/worker host-tool invocation; review every call for necessity
and correct role ownership. CLI/Desktop parity requires consecutive successful real-host
runs on one unchanged payload. State an unavailable host as unverified.
