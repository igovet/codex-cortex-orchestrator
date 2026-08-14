# Cortex

Cortex is a repo-source Codex plugin for explicit, durable orchestration. It
ships 21 agent profiles, 10 skills, the local `cortex` MCP server, and
privacy-limited lifecycle hooks. It is schema `cortex/v7` and plugin version
**1.0.2**. This is a breaking ledger upgrade: older tasks and lanes have no
compatibility reader and must be recreated as v7 records.
The bundled `plugins/cortex/skills/orchestrator/SKILL.md` is the single authoritative
source for the main Cortex skill. All installable profiles, skills, hooks, MCP
configuration, and runtime code live below `plugins/cortex/`; root-level
scripts, tests, and docs support repository development only.

## Install, update, and verify

Cortex requires Codex multi-agent v2 for explicit per-subagent model routing,
including forcing a Luna worker from a Terra or Sol coordinator. Enable it in
`~/.codex/config.toml` before starting Codex:

```toml
[features]
multi_agent_v2 = true
```

After changing this setting, start a new Codex task. Existing tasks retain the
multi-agent adapter selected when they were created; with v1, an explicit
`gpt-5.6-luna` override is rejected. Cortex uses the v2 adapter to dispatch
Luna explicitly for eligible lightweight work.

The main coordinator must also pass the exact model identifiers accepted by
the native `spawn_agent` host as `available_models` on each delegation. When
the host does not expose Luna but does expose Terra, Cortex records the
policy preference for Luna and dispatches Terra with
`fallback_reason: host_model_unavailable`. It never labels that worker as
Luna. Missing Sol or other non-fallback policy routes remain blocked.

When the user explicitly requests a separate, visible Luna task, the
coordinator may choose `dispatch_mode: visible_thread` instead. It must pass
the exact `create_thread` catalog as `available_thread_models`; Cortex then
emits a `create_thread` request with Luna and the routing policy's calculated
reasoning effort (it does not force `max`). This is an opt-in, user-owned task,
not a hidden subagent and not an automatic fallback.

If the user explicitly requires a Luna task when `spawn_agent` cannot accept
Luna, the coordinator passes `luna_fallback: visible_thread` together with both
host catalogs. Cortex retains a hidden Luna dispatch where possible; otherwise
it returns a visible `create_thread` Luna request and never degrades that
explicit fallback to Terra.

Run one command from this repository:

```bash
./scripts/sync-cortex.sh
```

It validates the repository marketplace and plugin, removes only exact known
managed legacy artifacts after a backup, registers the repo-local marketplace,
reinstalls Cortex, and verifies same-version file content. Cleanup is limited
to the known profile hash, an authenticated retired 4.4.0 cache layout, and the
exact retired local marketplace entry. Unexpected files, symlinks, versions,
or paths cause refusal. Preview or check without changing the installation:

```bash
./scripts/sync-cortex.sh --dry-run
./scripts/sync-cortex.sh --check
```

For rollback, remove the new install with `codex plugin remove cortex@cortex`.
Managed legacy artifacts removed during upgrade are copied first under
`$CODEX_HOME/backups/cortex-upgrade/` in a collision-safe private backup slot.
The installer enforces mode `0700` on the Cortex upgrade directory and removes
group/world permissions recursively from each completed backup slot.
Those backups are local operator data: they are never uploaded or included in
the repository release archive. Restoring or deleting them is an explicit
manual operation so unrelated user-owned configuration is never overwritten.

Start a **new Codex thread** after installing or updating so its skills and MCP
tools are picked up. Test an isolated fresh registration with:

```bash
python3 scripts/probe-fresh-cortex-plugin.py
```

The repository marketplace is `.agents/plugins/marketplace.json` at the
repository root.
It is explicitly registered by the installer; no personal marketplace is used
for the new installation.

## Release boundary

The repository package is ready for local validation, not for publication by
default. The blocking release check builds a fresh `git archive HEAD` and
rejects runtime ledger state, bytecode, symlinks, nested marketplace artifacts,
and secret-prone paths before validating the package again. This checkout has
an unborn `HEAD`, so `python3 scripts/verify-cortex-release.py` reports `SKIP`
and `python3 scripts/verify-cortex-release.py --require-tracked` fails by
design. Create the initial commit only with explicit authorization, then run
the blocking check against that committed tree before any push, tag, or catalog
submission.

See [release readiness](docs/release-readiness.md) for the external gates:
verified public-manifest schema, confidential vulnerability reporting route,
immutable-tag clean install, remote provenance, and catalog authorization.

## Usage and activation

### Codex Desktop

Open the **Skills** picker, select **Cortex Orchestrator** (`cortex:orchestrator`), and state the
task. You can also mention the skill in chat with `$cortex:orchestrator`. Add `help`,
`harvest`, `harvest-refresh`, or `normal` after the skill name when needed; no
argument starts ordinary task orchestration.

```text
$cortex:orchestrator Add tests for the billing retry flow.
$cortex:orchestrator help
$cortex:orchestrator harvest
$cortex:orchestrator harvest-refresh
$cortex:orchestrator normal
```

### Codex CLI

Lead with a skill mention:

```text
$cortex:orchestrator Add tests for the billing retry flow.
```

Alternatively, enter `/skills`, select `cortex:orchestrator`, then provide the task
or one of the same arguments: `help`, `harvest`, `harvest-refresh`, or
`normal`.

Cortex does not provide native bare `/cortex` or `/normal` commands. `/cortex`,
`/cortex help`, `/cortex harvest`, `/cortex harvest-refresh`, and `/normal`
are textual shorthand only when a host passes them through; a host may reserve
or reject them. Do not use the deprecated `/prompts` mechanism. Selecting the
Cortex skill for any non-help, non-`normal` route explicitly activates it;
`$cortex:orchestrator normal` exits an active session. Ordinary requests and mere
mentions remain normal workflow and do not create a ledger. Help is read-only
and never activates Cortex.

The knowledge routes delegate source inspection to a read-only `explorer` and
bounded documentation changes to `technical_writer`. `harvest` changes only
missing or stale facts justified by evidence. `harvest-refresh` re-audits all
allowed knowledge docs, preserves manual notes outside generated blocks, runs
the full applicable verification set, and requires a no-change second planning
pass before completion. Both routes reconcile the project manifest and finish
with a handoff.

For every active Cortex task, the coordinator supplies an explicit absolute
`project_root` on activation. That first call immutably binds the MCP process;
later calls may repeat the same root or safely omit it. Before any
project read, search, edit, command, test, or worker dispatch, Cortex must
successfully activate, classify, initialize, and read status from
`${project_root}/.codex/cortex`. An unavailable MCP server, failed
initialization/status call, unwritable or mismatched root, a set
`CORTEX_ROOT`, or a `/tmp` fallback is a hard blocker; Cortex stops and
reports it rather than running an unledgered fallback. `CORTEX_PROJECT_ROOT`
is not a substitute for the activation argument. When calling the installed
server through JSON-RPC, launch
`python3 /absolute/plugin/path/scripts/cortex.py` and include `project_root`
in the activation `tools/call.arguments` object. Stale delegation revisions,
reused status receipts, and stale requested gates are corrected against the
serialized ledger state and reported as correction metadata rather than MCP
errors. Premature gate passes and missing-but-ambiguous evidence links return
`recorded: false` with a machine-readable `next_action`.
Every MCP tool failure is also appended as a redacted JSONL record under
`~/.codex/logs/cortex-tool-errors.jsonl`. Each record includes the tool input
summary, chat/thread session id, JSON-RPC request id, and any task/attempt or
other call ids that were present; the log directory is `0700` and the file is
`0600`.
The classification receipt is also authoritative for the initial pipeline:
the main orchestrator chooses the complete optional gate list from
`available_gates` and passes it as `classify_task.pipeline`. Cortex validates
those ids and appends only the mandatory `documentation` and `close` gates;
the canonical gate IDs are `plan`, `discover`, `architecture`,
`database_architecture`, `implementation`, `qa`, `security`, `performance`,
`accessibility`, `ux`, `review`, `documentation`, and `close`. For adapter
compatibility, bounded aliases such as `planning`, `discovery`, and
`verification` are normalized to `plan`, `discover`, and `qa`; unknown IDs
still fail closed.
duplicate, truncated, malformed, or reordered `init_task.pipeline` input is
ignored and reported through `pipeline_correction`, so mandatory gates cannot
disappear because of a model-generated duplicate field. Calls that omit
`pipeline` remain supported as a legacy heuristic fallback, but the shipped
orchestrator instructions always provide the full proposal. During execution,
`reassess_pipeline` accepts another full replacement and can add, remove, or
reorder gates. The orchestrator may also provide ordered `parallel_groups`
waves; independent gates in the current wave can run concurrently, while the
next wave waits for every active gate. Removing a completed gate requires
explicit `allow_rework`.

Textual shorthand examples (only if the host accepts them; not native slash
commands):

```text
/cortex Составь план и проведи задачу через Cortex.
/normal
```

An ordinary complex request, a request mentioning orchestration, or using
subagents does **not** activate a durable ledger. After activation, the main
agent remains user-facing while internal workers inspect, implement, test, and
report through the main chat.

Workers first call `mcp__codebase_memory__list_projects` and match the exact
absolute project root to an indexed project before using any other
codebase-memory tool. They pass the returned project identifier/path forward,
never guessing it; then they use graph search for definitions and
relationships, code search for text, path tracing for callers and data flow,
and symbol snippets after an exact qualified name is found. If the listing
fails or no indexed project matches, workers do not call other codebase-memory
tools, record the limitation, and may use another search method only as a
documented fallback. They must never claim codebase-memory evidence they did
not obtain.

Codebase-memory usage is deterministic: call `list_projects({})` first, match
the exact `root_path`, and pass the matched record's `name` as `project`.
Use `index_status` for freshness, `search_graph` for symbols and relationships,
`search_code` for text, `trace_path` for callers/data flow,
`get_code_snippet` for an exact symbol, `get_architecture` for an overview, and
`query_graph` only for explicit multi-hop Cypher queries. Indexing, trace
ingestion, ADR management, and project deletion are state-changing operations,
not ordinary search tools.

### Recommended codebase-memory-mcp integration

For substantially better repository discovery, we recommend installing and
configuring [DeusData/codebase-memory-mcp](https://github.com/DeusData/codebase-memory-mcp).
Cortex has a native integration with this MCP and routes code-search work
through it when the indexed project is available. Workers always start with
`list_projects({})`, select the project whose `root_path` exactly matches the
current absolute `project_root`, and then use `index_status`, `search_graph`,
`search_code`, `trace_path`, `get_code_snippet`, `get_architecture`, or
`query_graph` as appropriate. If the MCP is unavailable or the project is not
indexed, Cortex records that limitation and uses a documented fallback instead
of claiming codebase-memory evidence that was not obtained.

## Profiles, routing, and reports

`plugins/cortex/profiles.json` is the runtime contract for the 21 bundled
profiles and their exact names. `task_formatter` is not a supported profile.
Every delegation records the requested and selected model and reasoning
effort. With required multi-agent v2 enabled, every delegation is routed
independently from its declared work intent, profile, and risk. Luna handles
reading, discovery/data gathering, investigation, diagnosis, research, code
review, CRUD-level edits, and small fixes whenever the `task_kind` declares
that intent, regardless of low/moderate/high/critical risk or the parent
task's C1/C2/C3 classification. A read-only profile alone does not force Luna:
non-analysis work such as implementation, architecture, migration, or
debugging remains Terra. Security task kind,
the security gate, and the
`security_auditor` profile always use Sol; contradictory task kinds are
normalized to security. A non-security Sol route must carry a structured
`sol_escalation`: a supported auditable extreme criterion with an `audit_ref`,
or a `terra_failure` linked to a failed Terra attempt in the current ledger.
Free-form `escalation_reason` text is preserved as context but never grants the
exception. The supported auditable-extreme criteria are
`irreversible_multi_system_recovery`, `safety_critical_incident_response`,
and `novel_cross_system_failure_without_bounded_rollback`. Reasoning effort is
selected independently of the routing category: requested effort `none`
becomes `low`; for Luna analysis/lightweight work the minimum/default is
`medium` at low/moderate risk, `high` at high risk, and `xhigh` at critical
risk, while explicitly higher requested effort is preserved. Sol uses at least
`high`.

Each delegation first produces an `awaiting_host_spawn` intent plus a complete
native `spawn_agent` request (or, for an explicitly authorized visible Luna
task, a `create_thread` request). The main Codex agent calls that host tool, then
records its returned child id, actual `host_model`, and actual
`host_reasoning_effort` with `confirm_host_spawn`; only model-verified
confirmation may make the attempt running or allow successful completion. A
missing host model is recoverable, while a requested/actual model mismatch
(such as Luna requested but Terra started) terminalizes the attempt as failed
with `host_model_mismatch` and cannot be reported as a successful Luna worker.
A native spawn failure is finalized as a non-success attempt rather than being
represented as a running worker. The recorded child id is coordinator-supplied
correlation, while Desktop/CLI host activity remains the source of truth for
the actual worker. Each worker
publishes exactly the `cortex/report/v1` fields: `summary`,
`findings`, `questions`, `changed_files`, `tests`, `evidence`, `uncertainty`,
and `next_action`. `record_report` stores sanitized authoritative JSON, creates
a one-use attempt receipt, updates task- and delegation-scoped indexes, and
generates an escaped Markdown view. Evidence consumption creates an
irreversible `reports/consumptions/` tombstone; reconciliation may repair
derived receipts and indexes but never makes a consumed receipt reusable.
The spawn briefing supplies the exact canonical `attempt_id` and a lowercase
stable `submission_id`. For native-worker recovery, either identifier may be
omitted or empty only when the worker identity maps to one active attempt;
Cortex then infers the attempt and derives a deterministic submission id.
Ambiguous or malformed identifiers remain fail-closed. The coordinator must
monitor every worker through host completion, task status, report/question
buses, and finalization, preserving exact Cortex tool errors in the report or
terminal-attempt reason.
Worker execution is English-only: internal prompts, Cortex arguments, reports,
questions, handoffs, and audit records are English. The main coordinator uses
the user's task language (or explicit `user_language`) for all user-facing
questions and summaries; localized display text does not replace the durable
English record. Typed fast paths reduce round-trips without weakening receipts
or locks: `prepare_delegation`, `prepare_delegations` for independent
same-wave workers (including multiple gates in `parallel_groups`),
`complete_attempt`, `commit_gate`, and `close_audit`.
Legacy calls remain available for older adapters and recovery.
If a host adapter submits a unique context-grant id where a report receipt is
expected, the server corrects it to the attempt-bound receipt. Other
`commit_gate` and `complete_attempt` validation failures are persisted as
bounded recovery events; after three failures for the same gate/mode, Cortex
marks the task `blocked` and returns a handoff/resume action instead of
allowing an active retry loop.
Individual files are atomically replaced; the whole multi-file publication is
not crash-atomic. Report bodies are task-bound and require an explicit
per-attempt context grant. Hooks inject the report contract and internal-worker
routing only for an active, initialized task; they remain best-effort telemetry,
not proof that the host spawned an agent.
Report intake is bounded to 64 KiB and 100 list items per field; a task keeps
at most 256 reports / 1 MiB aggregate, and telemetry retains at most 1,000
events / 512 KiB. Ledger paths reject symlink ancestry and regular-file
replacement targets, so coordination data cannot be redirected through a
symlink.

## Questions in the main chat

Use `cortex.question` whenever the coordinator or a worker reaches a material
branch, approval, or missing requirement. A worker supplies its `attempt_id`
and stable `submission_id`; Cortex records the question and the main agent
surfaces it with `list_worker_questions` followed by `cortex.question` using the
returned `question_id`. Hidden workers never open a user-facing form and must
wait for `get_worker_question_updates` after publishing a blocking question.

The native form supports one-choice options, multi-choice checkboxes via
`multiple: true`, and always appends a final `custom_response` field for free
text or additional context. Returned structured content is retained so host
attachment/image metadata is not discarded. The main agent owns the decision;
workers must not resolve an unresolved user branch themselves.
Several workers can ask questions at once. The durable bus assigns a global
sequence while retaining each `attempt_id`; the coordinator answers each open
question independently in sequence order and keeps polling until `open_count`
reaches zero.

## Validation

```bash
python3 -m unittest discover -s tests -v
python3 scripts/cortex-cold-boot-smoke.py
python3 scripts/probe-fresh-cortex-plugin.py
python3 scripts/cortex-composite-benchmark.py --workers 8
python3 scripts/validate-cortex-marketplace.py
python3 scripts/verify-cortex-release.py --require-tracked  # requires a committed HEAD
# Optional: run the plugin-creator validator from its installed skill directory.
bash -n scripts/sync-cortex.sh
```
