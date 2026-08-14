# Decisions

## Ordered waves with parallel attempts

The ledger keeps an ordered wave sequence because it makes dependencies and
completion decisions auditable. Independent gates and multiple agents for one
gate may run concurrently inside the active wave; every passed attempt must
produce linked evidence before the wave advances. Dependent work belongs in a
later wave. A general DAG would be a separate schema decision rather than an
implicit reinterpretation of the current state model.

## One-tool public coordinator facade

The breaking 2.0 public API exposes only `orchestrate`. A coordinator starts a
task with one `start` operation, then advances exactly once for each completed
parallel wave. Start owns activation, classification, ledger initialization,
full-plan persistence, and first-wave preparation. Advance validates all
parallel completions before state writes, records strict eight-field
`cortex/report/v1` reports, evidence, gates, and actual host fields, and
returns the next wave. It can replace future waves; changing a completed gate
requires explicit `allow_rework`.

The final advance reconciles reports and the project manifest, records the
documentation decision, verifies close evidence observed by the server,
creates the handoff and audit record, and completes the task. `inspect`,
`resume`, `deactivate`, `lane`, `resource`, and `question` are recovery or
nested operations of the same facade. Legacy v7 primitives remain internal;
existing v7 task data stays inspectable and resumable. This reduces public
lifecycle coupling while retaining the durable ledger's auditability.

Each mutating request is keyed by a durable `operations/<submission_id>.json`
digest receipt. Identical retries replay safely; reusing the id with changed
payload conflicts. This is deliberately per project root: every call carries
an absolute `project_root`, and one server process can serve multiple roots.

## Atomic records, repairable projections, best-effort telemetry

Task state, evidence, gate outcomes, handoffs, and authoritative report JSON
use locked, fsync-backed per-file atomic replacement. Report Markdown and
indexes are projections that `reconcile_report_bus` can rebuild from validated
records. Related files are not one crash-atomic transaction. Lifecycle hook
telemetry and model metrics are observational and must never block task
execution; their durability is intentionally weaker.

## Bounded gate recovery

Composite gate commits must not turn adapter mistakes into an active task that
can be retried forever. A unique context grant may be normalized to its
attempt-bound report receipt because the server can verify task, gate, attempt,
and one-use ownership. Any other repeated `commit_gate` validation failure for
the same gate/mode is persisted as a recovery event; after three failures the task is
blocked with a handoff/resume action. This preserves the exact failure for
repair while guaranteeing a terminal control-plane outcome.

## C2/C3 proof requirements

C2 and C3 tasks require delegation-linked evidence and a final handoff before
completion. Schema v7 additionally requires a consumed classification receipt,
a status observation before delegation, with stale revision/receipt hints safely corrected by the serialized server, a `technical_writer` documentation
decision, an explicit reassessment decision, server-observed successful command
evidence at close, a complete project-manifest receipt, and an attempt-tied
single-use worker-report receipt for delegated evidence. C1 remains lightweight
and may use direct evidence for local work.

Reworking a gate invalidates that gate and every downstream gate, including
their prior evidence, so a later pass cannot accidentally reuse stale proof.

## Manifest-backed handoffs and bounded host correlation

Each v7 task captures a project file manifest at initialization and compares it
at reconciliation or handoff. A final handoff must name every detected changed
file, including additions, deletions, modifications, and recognized renames.
This makes touched-file reporting checkable without relying on a worker's
summary.

The ledger stores `principal` and `thread_id` for authorization and a
delegation attempt for auditability, but it deliberately labels the link as
`ledger_attempt_only`: the local server has no trustworthy host-side spawn
attestation. Hooks therefore record sanitized lifecycle context and canonical
agent-name guidance only. State mutations are lock-serialized and each JSON
replacement is fsync-backed; related task, lane, index, and journal writes are
not a single crash-atomic transaction. These limits keep the guarantees
explicit rather than overstating local-file durability.

The confirmed host child/thread id is additionally indexed as a narrow alias
for that attempt's worker report. It is never accepted as a coordinator
principal and cannot authorize task, gate, evidence, or pipeline mutations.

## Optional execution lanes

The task ledger remains the default orchestration surface. A lane is an
optional durable execution container for persistent or multi-repository work.
It owns a lease, portable declarations, and cross-task resource claims, but it
may materialize only explicitly declared Git worktrees under a live lease and
explicit confirmation. It never uses force removal, refuses dirty retirement,
and does not launch arbitrary processes. This keeps C1 work lightweight while
providing Mandate-style recovery and collision control for C3 work.

## Explicit activation

The Cortex control plane is inactive by default. In Desktop, select
`cortex:orchestrator` through the Skills picker or mention
`$cortex:orchestrator`; in CLI, lead with `$cortex:orchestrator` or use
`/skills` and select it. Non-help,
non-`normal` skill routes authorize the main/root agent to pass the server's
canonical `/cortex` token. The `normal` skill argument deactivates the mode.
Literal `/cortex` and `/normal` are textual shorthand, not registered native
slash commands. Classification, task creation, delegation, gates, lanes, and
claims cannot mutate state before activation.

## Visible-thread checkout selection

Visible `create_thread` dispatches are user-owned tasks created by the host,
not hidden `spawn_agent` workers. Cortex records the selected profile and
model in the request and supplies the profile instructions in the generated
prompt. Their checkout is explicit: `thread_environment` defaults to `local`
so a read-only visible task stays in the saved project, while callers can
request `worktree` for concurrent or write-heavy work. The coordinator maps
the value to the native `target.environment.type`; local sharing is a
deliberate trade-off and requires serializing writers.

## Project-local runtime state

Production orchestration is fail-closed for each supplied absolute
`project_root`; one MCP server process can serve multiple roots. `orchestrate`
validates the selected root before it prepares work, and an unavailable server
or failed, unwritable, or mismatched root ends that task's workflow with a
blocker. Ordinary/unledgered subagent work is not a substitute.

## Skill-level Cortex routes

The supported native entry is the host-discovered `cortex:orchestrator` skill
(or `$cortex:orchestrator` prompt reference). Cortex subcommands are deterministic skill
arguments, not separately registered slash commands. An empty argument selects
ordinary task orchestration; `help`, `harvest`, `harvest-refresh`, and `normal`
select the other routes. Help is read-only; normal deactivates session state
without creating a task. Knowledge routes retain the v7 task, delegation,
gate, project-manifest, verification, and handoff contracts.

## Bundled profile contract and capability-aware routing

`plugins/cortex/profiles.json` is the single machine-readable source for the 21
supported profile names, sandbox modes, automatic gate routes, and the shared
worker report contract. The removed `task_formatter` profile is not accepted by
the server. Model selection remains a main-agent dispatch decision, but Cortex
resolves it against the current host capabilities and persists requested,
selected, policy, and fallback fields. With multi-agent v2 enabled, every
delegation is evaluated independently from its declared work intent and risk.
Luna handles explicit reading, discovery/data gathering, investigation,
diagnosis, research, code review, CRUD-level edits, and small fixes at any
risk; a read-only profile alone does not imply Luna. Terra is the initial
policy for non-analysis work such as architecture, migration, debugging, and
implementation, then the exact model/effort remapping table is applied before
dispatch. Security task kind, the security gate, and the `security_auditor`
profile initially select Sol, then follow that same table, normalizing
contradictory task kinds to security. A non-security Sol exception must be structurally auditable: a supported
extreme criterion with an audit reference, or a ledger-validated failed Terra
attempt. The supported auditable-extreme criteria are
`irreversible_multi_system_recovery`, `safety_critical_incident_response`,
and `novel_cross_system_failure_without_bounded_rollback`. Luna
analysis/lightweight work has a `medium` minimum/default at low/moderate risk,
`high` at high risk, and `xhigh` at critical risk; explicit higher effort is
preserved. Other reasoning effort is selected independently of routing;
`none` normalizes to `low`, and the five exact remapping pairs are applied
before host capability selection. The coordinator passes the exact
`spawn_agent` catalog and, after a fresh install, the confirmed
`spawn_agent_default_model`. A Luna route prefers that configured default,
then an explicit Luna override, and finally an explicit hidden Terra override.
Automatic visible-thread fallback is not part of model routing.

## Scoped worker report bus

Workers publish a strict eight-field `cortex/report/v1` payload through public
`advance` completions; the private v7 report primitive stores the canonical
sanitized JSON record, which is task- and attempt-bound;
submission ids make retries idempotent. A receipt links one report to one C2/C3
evidence record and is consumed once. Its `reports/consumptions/` tombstone is
irreversible and prevents replay even if reconciliation repairs derived files.
The task index exposes metadata only.
Delegation indexes separate reports owned by an attempt from report bodies
explicitly granted as context. This keeps cross-worker context intentional and
bounded while acknowledging that local principal/thread values are
caller-asserted, not host identity attestation.
