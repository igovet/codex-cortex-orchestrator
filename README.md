# Cortex

Cortex is a repo-source Codex plugin for explicit, durable orchestration. It
ships 21 agent profiles, 10 skills, the local `cortex` MCP server, and
privacy-limited lifecycle hooks. It is schema `cortex/v7` and plugin version
**3.3.0**. The public MCP surface has exactly six tools: three coordinator
lifecycle operations—
`start_orchestration`, `continue_orchestration`, and
`manage_orchestration`—plus worker `worker_question` and `record_report`, and coordinator
`read_worker_report`; existing v7 ledgers remain readable through a private
compatibility adapter. Ledgers older than v7 have no compatibility reader and
must be recreated.
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
`gpt-5.6-luna` override is rejected. Cortex uses the v2 adapter to apply its
per-worker Luna/Terra/Sol policy and coordinator-selected effort.

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

Start a **new Codex thread** after installing or updating before dispatching
agents. Existing threads can retain absolute paths to lifecycle hooks in the
retired cachebusted plugin directory. Cortex hook commands now detect that
missing script and return the empty JSON object `{}` successfully, without the
Python missing-file error; the stale thread simply receives no hook context or
telemetry from that call. A new thread is still required to pick up the updated
skills, hooks, and MCP tools. Test an isolated fresh
registration with:

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
and secret-prone paths before validating the package again. The Cortex 3.3.0
changes in this working tree are intentionally uncommitted, so
`python3 scripts/verify-cortex-release.py --require-tracked` still validates the
previous `HEAD` and fails its 3.3.0 package contract. Commit only with explicit
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

The knowledge routes build a source-backed exhaustive feature census, not a
summary of recent changes. Both use the canonical `plan`, `discover`,
`architecture`, `documentation`, `review`, and `close` phases. `harvest` is
incremental only when `docs/features/index.md` already contains a current,
zero-gap coverage manifest; otherwise it runs a full baseline census.
`harvest-refresh` always rebuilds the inventory from source and requires an
independent source-to-doc completeness review with zero unexplained unmapped
surfaces and a no-change second documentation plan. Large repositories split
discovery across 2–8 non-overlapping domain explorers and may parallelize
technical writers only across non-overlapping documentation paths, with one
owner for the coverage manifest. Both routes preserve manual notes outside
generated blocks, reconcile the project manifest, and finish with a verified
handoff. During `documentation`, `review`, and `close`, Cortex structurally
validates `docs/features/index.md` and rejects a shallow link list that omits
the Coverage matrix columns, Inventory totals, Unmapped surfaces, Exclusions,
or Known unknowns sections.

For every Cortex call, the coordinator supplies the exact absolute
`project_root`; it is never inferred from the server process, environment, or
working directory. One MCP process may serve multiple project roots, while
each activation and task remains authorized only by its own
`${project_root}/.codex/cortex` record. A missing, relative, symlinked,
unwritable, or mismatched root, a set `CORTEX_ROOT`, or a `/tmp` fallback fails
closed before task state is created.

The public normal flow has a narrow lifecycle plus scoped report transport. A minimal
`start_orchestration` call contains only the absolute `project_root` and
`task.objective`; complexity safely defaults to C2 and Cortex constructs the
pipeline. Optional compact overrides use `waves[].workers[]`, where only
`phase` is required, `depends_on` selects exact prerequisite phases, and
`context_files` carries task-relevant project or feature documentation.
Omitting `depends_on` supplies all verified predecessor reports; an empty list
marks an intentionally independent worker. Common human language names
normalize to compact tags before ledger creation; in particular, `implement` maps to `implementation`
and `build_verification` maps to the final `close` phase, avoiding repeated
correction/retry loops around those common labels. `continue_orchestration` is called once per completed
wave with the prior relative `step` and worker results. A sequential result
needs no worker reference; a parallel wave uses only the returned slots
`worker: 1..N`. Any worker can persist a material user decision through
`worker_question`, pause without completing its attempt, receive the answer on
the same `question_ref`, and resume the same native worker. Open blocking
questions reject both report publication and wave continuation. Workers then
persist all eight report sections with `record_report`,
return only `REPORT_RECORDED report_ref=<value>` plus at most a two-sentence
summary (or the exact report-tool error), and the coordinator reads each ref
with `read_worker_report`. If a native acknowledgement is interrupted after
persistence, `manage_orchestration` inspect exposes the ref in
`available_reports`. Slots and report refs are validated atomically
before lifecycle state is written.

Every task-bound lifecycle response returns an opaque `task_ref`. Preserve it
on every later `continue_orchestration`, `manage_orchestration`, and
`read_worker_report` call. Different task contracts may run concurrently below
one project root; an exact duplicate active start is idempotent and explicitly
returns `replayed: true` with no dispatches, while changed
task or wave content creates a distinct task. If a later call omits the ref
while several tasks are selectable, Cortex returns `needs_selection` with
opaque refs and objectives instead of guessing.

Caller-generated submission, task, wave, attempt, coordinator, and host IDs
do not cross the normal-flow boundary. Cortex owns transaction idempotency:
an identical retry replays, a changed payload conflicts, and a stale relative
step is rejected. `manage_orchestration` keeps inspect, resume, deactivate,
lane, resource, durable-question recovery, and confirmed `prune` maintenance
outside the common path.
The coordinator owns the pipeline: it builds or consciously accepts the
initial waves, follows the returned `pipeline` snapshot by default, and alone
decides whether verified evidence justifies changing `future_waves`, with a
concise reason. Planner and explorer findings are advisory. Semantically
unchanged reassessment is accepted as unchanged and keeps subsequent relative
steps monotonic. A completed response explicitly
reports `close_verified` and `handoff_ready` so Luna does not start a second
run merely to rediscover terminal proof.
Existing `cortex/v7` ledgers remain inspectable and resumable through this v3
adapter; the legacy `orchestrate` facade is not published in `tools/list`.

Workers never call Cortex lifecycle operations. They call scoped
`worker_question` for unresolved material user decisions, use the native
parent/child channel only to return its compact ref and receive the resume
signal, and call scoped `record_report` for their strict
eight-field `cortex/report/v1`, and never paste that JSON into the native final
response. Expected validation
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

While Cortex is active, the main/root agent is coordination-only. It must not
inspect, search, read, edit, patch, build, test, or run the target project and
must remain idle while a worker is active. It may call Cortex, launch the exact
returned dispatches, wait, evaluate reports, route questions, and communicate
with the user. Worker failure or delay is handled as rework or a blocker, never
by falling back to direct root implementation.

Codebase Memory is an optional worker accelerator, not a root-coordinator
capability. When its tools are actually present, a worker first calls
`list_projects` and selects only the entry whose root exactly matches the task
project. It should prefer graph, architecture, and trace operations for
discovery and impact analysis, then confirm consequential findings in current
source and tests. `planner`, `explorer`, `architect`, and `database_architect`
may perform one bounded refresh when the exact index is missing or stale;
every other profile falls back after one failed attempt to normal repository
tools without setup loops. The coordination-only root never uses Codebase
Memory to inspect the target project itself.

When present, Cortex automatically adds `docs/project/index.md` and
`docs/features/index.md` to every worker briefing. The planner reads those
indexes first, selects all task-relevant linked pages, and recommends their
exact paths; the coordinator attaches that evidence-backed selection to later
workers through `context_files`. Every worker re-checks the indexes, treats
documentation as navigation and prior knowledge rather than authority, and
confirms consequential claims in current source, tests, schemas, or executable
configuration. Its persisted report includes one `Knowledge reviewed:`
evidence entry naming both available indexes and every additional page used;
public `record_report` rejects a missing index acknowledgement. Explicit
`context_files` must resolve to existing project-relative regular files;
absolute, traversing, missing, and symlink paths are rejected.

## Profiles, routing, and reports

`plugins/cortex/profiles.json` is the canonical machine-validated contract for
all 21 bundled profiles. Each entry defines the exact name, description,
sandbox, automatic/manual route category, owned gates, `select_when`, and
`avoid_when`; the matching TOML must agree on identity, description, and
sandbox. Each profile carries a structured professional playbook. A generated worker briefing then combines that playbook with the
overall task context and the current gate's mission, ownership, acceptance, and
verification defaults. Task-level criteria remain separate from gate-level
criteria; explicit coordinator overrides take precedence over gate defaults,
and omitted values are filled from the validated 13-gate registry. The
`planner` profile is read-only and must ground a decision-complete plan in
repository evidence, resolve discoverable facts before asking questions, and
leave no material implementation decisions for the executor. `task_formatter`
is not a supported profile.

Automatic implementation routing conservatively scans bounded explicit
signals in the objective, requirements, acceptance criteria, scope, allowed
paths, and verification, including relevant English and Russian terms. The
ordered specialist precedence is `fullstack_dev`, `mobile_dev`,
`devops_engineer`, `data_engineer`, `debugger`, `refactorer`, `frontend_dev`,
then `backend_dev`; `general` is used only when no specialist signal is strong
enough. This initial route is provisional: repository evidence from `planner`
or `explorer` is advisory, and the coordinator alone decides whether it
justifies replacing not-yet-started `future_waves`. Both worker profiles
receive the complete generated team catalog, and the root
orchestrator skill carries the synchronized generated roster and evidence-led
routing rules while remaining coordination-only.

The public compact worker schema advertises an exact enum of all 21 canonical
profile names. Legacy aliases are accepted only as runtime compatibility input,
and a profile that cannot own the requested phase is rejected before ledger
writes. Every returned dispatch exposes `worker`, `phase`, `profile`,
`capability`, `sandbox`, and `selection_reason` alongside `call` and unchanged
native `arguments`.
Every delegation records requested/expected model metadata separately from
the native request override and always records reasoning effort. `explorer`
always selects Luna; its effort is chosen by the coordinator or defaults from
risk, and Terra is reserved for a hidden host-unavailable fallback. The only
valid efforts are `low`, `medium`, `high`, `xhigh`, and `max`; `max` is the hard
upper bound. `planner` and all remaining non-security profiles default to Luna
at exactly `max`, while the coordinator may normally select Terra from `medium`
through `max`. Luna `max` is already a powerful default and should not be
escalated reflexively. Security
context, the security gate, and `security_auditor` always select Sol, with
effort floors C1 `medium`, C2 `high`, and C3 `xhigh`, capped at `max`.

Non-security Sol is accepted only when the user explicitly selected it. Pass
compact `user_requested_model: sol`; omit `model` or also set it to `sol`.
Cortex records matching `user_requested_model` and `requested_model`.
Coordinator preference, auditable-extreme labels, and a failed Terra attempt
are not authorization;
the retired `sol_escalation` field and model/effort remaps are not part of the
current contract. A configured-default Luna request omits native `model` while
preserving the selected effort. Explicit model selections retain `model`; if
Luna is unavailable to the host, Cortex may dispatch hidden Terra without
changing the selected effort. Observed host metadata is stored separately,
and Cortex never claims an actual worker model from expected routing alone.

Each v3 dispatch is `{worker, phase, profile, capability, sandbox,
selection_reason, call, arguments}`. `arguments` contains only the real native
`spawn_agent` or `create_thread` parameters; routing and expected-model
metadata is never copied into native `model`. The main Codex agent invokes all
independent requests in a wave, waits for them, and submits their reports in
one relative continue call. A malformed report, duplicate/foreign slot, or
incomplete wave is rejected before partial acceptance. Native spawn failures
are submitted as explicit non-success results with a normalized status and
reason.

Each worker writes exactly the `cortex/report/v1` fields—`summary`,
`findings`, `questions`, `changed_files`, `tests`, `evidence`, `uncertainty`,
and `next_action`—through public `record_report`. The operation stores sanitized authoritative JSON, creates
a one-use attempt receipt, updates task- and delegation-scoped indexes, and
generates an escaped Markdown view. Evidence consumption creates an
irreversible `reports/consumptions/` tombstone; reconciliation may repair
derived receipts and indexes but never makes a consumed receipt reusable.
Worker briefings contain only the exact task and attempt identifiers required
for that one report write and explicitly forbid using them with lifecycle or
pipeline tools. The native result is only `REPORT_RECORDED report_ref=<value>`
plus at most a two-sentence summary, or the exact report-tool error; the
coordinator reads the durable report and maps its ref
to the current relative slot. Cortex privately creates
the durable report receipt, evidence, gate transitions, reconciliation, and
handoff. The coordinator waits for every native worker in the current wave
before calling `continue_orchestration`.
When predecessor handoffs are embedded in a dispatch, the worker reads every
one before project work, reconciles relevant findings and conflicts against
current evidence, and adds the generated `Predecessor review:` entry naming
every supplied report ref to report evidence. Public `record_report` rejects
an incomplete acknowledgement. Cortex also fails closed instead of silently
dropping predecessor reports when the safe count or context-size budget is
exceeded; narrow the dependency set with `depends_on`.
The public compact worker schema accepts `context_files`. Cortex also injects
the available project and feature indexes, and the report must include the
generated `Knowledge reviewed:` acknowledgement described above.
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

Questions are durable for every worker profile. A worker calls
`worker_question(action="ask")`, returns only `QUESTION_RECORDED` with the
`question_ref` and a concise summary, remains available, and does not record a
report. The main agent surfaces that exact ref with
`manage_orchestration(intent="question")`, collects the user's answer, then
signals the same worker to call `worker_question(action="poll")` and resume the
same attempt. Its private payload commands remain `ask`, `publish`,
`list`, `answer`, and `updates`; server-owned management transactions hide
their lifecycle identifiers from the normal coordinator path.

Every worker classifies an unknown before acting. Repository-answerable facts
are investigated; low-impact reversible details may be chosen and documented;
material product intent, behavior, audience, design direction, security,
irreversibility, or external commitments must be asked. Existing source proves
the current system, not the user's desired outcome. Cortex fails closed if a
worker tries to report or the coordinator tries to continue while a blocking
question remains open.

## Prune maintenance

The explicit `$cortex:orchestrator prune` route calls
`manage_orchestration(intent="prune",
payload={"confirmation":"PRUNE","older_than_days":7})` without a task ref.
It removes only task-scoped `.codex/cortex` records last updated at least seven
days ago, including abandoned active tasks, and reconciles task/v3 indexes,
activations, transaction and classification receipts, task resource claims,
and lane bindings. It preserves recent tasks, lanes themselves, project source,
documentation, and plugin files. This bounded weekly prune replaces the unsafe
idea of clearing the whole ledger.

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
python3 scripts/cortex-luna-high-eval.py
python3 scripts/probe-fresh-cortex-plugin.py
python3 scripts/cortex-composite-benchmark.py --workers 8 --waves 5
python3 scripts/validate-cortex-marketplace.py
python3 scripts/verify-cortex-release.py --require-tracked  # requires a committed HEAD
# Optional: run the plugin-creator validator from its installed skill directory.
bash -n scripts/sync-cortex.sh
```

The current 3.3.0 source candidate has 237 passing Python tests in 15.491
seconds; quick validation for the changed skills, plugin and marketplace
validation, Python compilation, and shell syntax also pass. Cachebuster
`3.3.0+codex.20260814224159` is installed and content-verified; installer check
and dry-run pass with `agents.default_subagent_model=gpt-5.6-luna`. Cold-boot
smoke, all three deterministic Luna-high fixtures, the eight-worker/five-wave
benchmark target, and the isolated fresh-plugin probe also pass for 3.3.0.
The earlier 3.2.x candidates remain historical evidence
for historical comparison only. The live Luna-high route, tracked release,
commit, tag, push, catalog publication, and
public release remain unverified.
