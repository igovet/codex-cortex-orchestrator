# Cortex

Cortex is a repo-source Codex plugin for explicit, durable orchestration. It
ships 21 agent profiles, 10 skills, the local `cortex` MCP server, and
privacy-limited lifecycle hooks. It is schema `cortex/v7` and plugin version
**2.0.0**. The public MCP surface is one `orchestrate` state-machine tool;
existing v7 ledgers remain readable through its `inspect` and `advance`
operations. Ledgers older than v7 have no compatibility reader and must be
recreated.
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

If a host exposes only Sol/Terra to hidden `spawn_agent` and has not confirmed
the global Luna default, Cortex stays hidden and falls back to an explicit
Terra subagent. It never creates a sidebar task as a model fallback.

### MCP tool approvals and auto-review

To approve every tool exposed by Cortex's `cortex` MCP server by default, add
this to `~/.codex/config.toml`:

```toml
[plugins."cortex@cortex".mcp_servers.cortex]
default_tools_approval_mode = "approve"
```

The `cortex` name is the MCP server name from the plugin's `.mcp.json`. A
server-level default also applies to tools added by later plugin versions. An
individual `[...tools.<tool>]` block can still override it, so remove the
per-tool `approval_mode` blocks if every Cortex tool should inherit the same
setting. This affects Cortex MCP calls only; shell, patch, and native Codex
tool approvals keep their own policies.

Keep approval review routed to the user instead of the automatic reviewer:

```toml
approval_policy = "on-request"
approvals_reviewer = "user"
```

Do not enable `auto_review`/`guardian_subagent` for this workflow and do not
use the `--approve-for-me` CLI option unless automatic review is intentional.
Auto-review invokes an additional model-based review for approval requests,
which consumes token and model budget; `approvals_reviewer = "user"` keeps the
decision manual.

The main coordinator passes the exact model identifiers accepted by native
`spawn_agent` as `host_capabilities.spawn_agent_models`. After a fresh host has
loaded the installed global setting, it also passes
`host_capabilities.spawn_agent_default_model = "gpt-5.6-luna"`. Cortex
validates every planned wave against those capabilities before creating task
state. A Luna route prefers the confirmed default and omits native `model`;
without that confirmation it uses an explicit Luna override when supported,
then an explicit hidden Terra fallback. It never labels Terra as Luna and
never creates a visible task as a fallback.

`dispatch_mode: visible_thread` remains a separate, explicitly selected
user-owned workflow; it is not accepted as `luna_fallback`. For hidden Luna
routes, the installer configures the global `[agents] default_subagent_model =
"gpt-5.6-luna"` setting. A configured-default hidden request carries
`expected_model = "gpt-5.6-luna"` and `model_resolution = "configured_default"`,
always includes `reasoning_effort`, and omits native `model`; explicit
Terra/Sol/Luna requests retain their `model` override. A visible task is
user-owned and appears in the sidebar, while a supported hidden Luna route
continues to use `spawn_agent`.

### Explicit visible-thread workspace

For a visible `create_thread` task, Cortex now emits
`spawn_request.thread_environment = "local"` by default. The coordinator maps
that value to the native request
`target.environment: { "type": "local" }`, so the task stays in the saved
project checkout instead of being moved to a managed Git worktree. To opt into
isolation for a writer or a concurrent task, pass
`thread_environment = "worktree"`; the coordinator then uses
`target.environment: { "type": "worktree" }`.

The visible task still runs with the selected Cortex profile in its generated
prompt (for example, `explorer`) and the routed model (for example, Luna), but
it is a separate user-owned Codex task rather than the hidden `spawn_agent`
worker. Local tasks share files, branches, and uncommitted changes with the
main checkout, so concurrent writers must be serialized. Existing worktree
tasks can be moved with the thread header's **Hand off → Local** action.

Normal Cortex routing always keeps the child out of the normal chat list. The
native `spawn_agent` route is hidden; when Luna cannot be resolved, Cortex uses
hidden Terra. `create_thread` is inherently user-visible and has no hidden
flag, so it is used only when the caller explicitly requests
`dispatch_mode = "visible_thread"` for reasons unrelated to model fallback.

Run one command from this repository:

```bash
./scripts/sync-cortex.sh
```

It validates the repository marketplace and plugin, removes only exact known
managed legacy artifacts after a backup, registers the repo-local marketplace,
reinstalls Cortex, and verifies same-version file content. Cleanup is limited
to the known profile hash, an authenticated retired 4.4.0 cache layout, and the
exact retired local marketplace entry. Unexpected files, symlinks, versions,
or paths cause refusal. If `~/.codex/config.toml` already contains Cortex's
`plugins."cortex@cortex".mcp_servers.cortex.default_tools_approval_mode`
override, the installer preserves that value across the remove/add cycle; it
does not create the override for users who have not configured it. The same
installer atomically enforces
`[agents] default_subagent_model = "gpt-5.6-luna"`. Before replacing a
different existing default it creates a private backup; comments, unrelated
keys, and file mode are preserved. Use
`--dry-run` to report the planned update without writing, or `--check` for a
read-only installed-content and legacy-artifact check:

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
and secret-prone paths before validating the package again. The Cortex 2.0
changes in this working tree are intentionally uncommitted, so
`python3 scripts/verify-cortex-release.py --require-tracked` still validates the
previous `HEAD` and fails its 2.0 package contract. Commit only with explicit
authorization, then rerun the blocking check against that committed tree before
any push, tag, or catalog submission.

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

For every Cortex call, the coordinator supplies the exact absolute
`project_root`; it is never inferred from the server process, environment, or
working directory. One MCP process may serve multiple project roots, while
each activation and task remains authorized only by its own
`${project_root}/.codex/cortex` record. A missing, relative, symlinked,
unwritable, or mismatched root, a set `CORTEX_ROOT`, or a `/tmp` fallback fails
closed before task state is created.

The public normal flow has only two operations. One
`orchestrate(operation="start")` call validates the installation, all 21
profiles, coordinator identity, task contract, full ordered wave plan, and
host model catalogs; it then privately activates, classifies, initializes,
and returns every native spawn request in the first wave. One
`orchestrate(operation="advance")` call per wave accepts every terminal host
completion, validates all actual host fields and strict reports before writing,
records reports/evidence/gates, optionally replaces not-yet-started
`future_waves`, and returns the next dependent wave. Reintroducing a completed
gate requires `allow_rework: true`. The final `advance` also performs the
documentation decision, server-observed close verification, report and file
manifest reconciliation, handoff, audit, and task completion.

Every mutating call uses a stable `submission_id`. An identical retry replays
its committed transaction receipt; reusing the id with different content is a
structured conflict. `inspect`, `resume`, and `deactivate` handle recovery and
session lifecycle. `lane`, `resource`, and `question` retain uncommon durable
subsystems as nested modes of the same public tool. Existing `cortex/v7`
ledgers can be reconstructed through `inspect` and continued through
`advance`; the old lifecycle names are private implementation details and
return `removed_in_v2_use_orchestrate` at the public JSON-RPC boundary.

Coordinator calls keep the task-bound `principal`/`thread_id` pair. Workers do
not call Cortex: they use the native parent/child channel for questions and
return one strict eight-field `cortex/report/v1` object. Expected validation
and recovery outcomes return `ok: false`, bounded diagnostics, and an exact
`next_action` without being appended to the exception log. Unexpected MCP
failures are appended as redacted JSONL under
`~/.codex/logs/cortex-tool-errors.jsonl`. Each record includes the tool input
summary, chat/thread session id, JSON-RPC request id, and any task/attempt or
other call ids that were present; the log directory is `0700` and the file is
`0600`.

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
Every delegation records requested/expected model metadata separately from
the native request override and always records reasoning effort. A
configured-default Luna request omits native `model`; confirmation and
`advance` use the actual host model as authority. With required multi-agent v2 enabled, every delegation is routed
independently from its declared work intent, profile, and risk. Luna handles
reading, discovery/data gathering, investigation, diagnosis, research, code
review, CRUD-level edits, and small fixes whenever the `task_kind` declares
that intent, regardless of low/moderate/high/critical risk or the parent
task's C1/C2/C3 classification. A read-only profile alone does not force Luna:
non-analysis work such as implementation, architecture, migration, or
debugging initially resolves to Terra and then follows the exact remapping
table above. Security task kind, the security gate, and the `security_auditor`
profile initially resolve to Sol; the same table is authoritative before
dispatch, and contradictory task kinds are normalized to security. A
non-security Sol route must carry a structured
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
`high` before the exact dispatch remapping below. The runtime applies only
these five pairs after normal policy resolution:

| resolved pair | dispatched pair |
| --- | --- |
| Terra + low | Luna + high |
| Terra + medium | Luna + high |
| Terra + high | Luna + xhigh |
| Sol + low | Terra + xhigh |
| Sol + medium | Terra + max |

Every other pair is preserved unchanged.

Each facade `spawn_request` contains its durable `attempt_id`, exact
profile/task name, native `host_tool`, expected model and resolution metadata,
an optional native model override, reasoning effort, and complete worker
prompt. The main Codex agent invokes all requests in that wave
in parallel when their ownership is independent. It then supplies the actual
host child id, tool, task name, model, reasoning effort, terminal status, and
report for every attempt in one `advance` call. A missing or mismatched host
field, malformed report, duplicate attempt, or incomplete wave is rejected in
preflight without partially accepting the batch. Native spawn failures are
submitted as explicit non-success completions with reasons.

Each worker returns exactly the `cortex/report/v1` fields: `summary`,
`findings`, `questions`, `changed_files`, `tests`, `evidence`, `uncertainty`,
and `next_action`. The private report primitive stores sanitized authoritative JSON, creates
a one-use attempt receipt, updates task- and delegation-scoped indexes, and
generates an escaped Markdown view. Evidence consumption creates an
irreversible `reports/consumptions/` tombstone; reconciliation may repair
derived receipts and indexes but never makes a consumed receipt reusable.
The spawn briefing supplies the exact canonical `attempt_id` and a lowercase
stable `submission_id`. For native-worker recovery, either identifier may be
omitted or empty only when the worker identity maps to one active attempt;
Cortex then infers the attempt and derives a deterministic submission id.
Ambiguous or malformed identifiers remain fail-closed. The coordinator waits
for every native worker in the current wave before calling `advance`.
Worker execution is English-only: internal prompts, Cortex arguments, reports,
questions, handoffs, and audit records are English. The main coordinator uses
the user's task language (or explicit `user_language`) for all user-facing
questions and summaries; localized display text does not replace the durable
English record. The v7 report, evidence, gate, and recovery primitives remain
available only inside the server so existing ledgers keep their invariants;
coordinators and workers must not call them directly.
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

Questions normally travel through the native parent/child channel and the main
agent surfaces them to the user. Use `orchestrate(operation="question")` only
when a pause must be durable across interruption. Its payload commands are
`ask`, `publish`, `list`, `answer`, and `updates`; mutating commands use a
stable facade `submission_id`.

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
python3 scripts/cortex-composite-benchmark.py --workers 8 --waves 5
python3 scripts/validate-cortex-marketplace.py
python3 scripts/verify-cortex-release.py --require-tracked  # requires a committed HEAD
# Optional: run the plugin-creator validator from its installed skill directory.
bash -n scripts/sync-cortex.sh
```
