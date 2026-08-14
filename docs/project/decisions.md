# Decisions

## Sequential gates with parallel attempts

The ledger keeps a sequential gate order because it makes the critical path
and completion decision auditable. Independent agents may run concurrently
inside the current gate by creating multiple delegations with `parallel=true`;
every attempt must produce linked evidence before the gate passes. A future DAG
would be a separate schema decision rather than an implicit reinterpretation of
the current state model.

## Atomic records, repairable projections, best-effort telemetry

Task state, evidence, gate outcomes, handoffs, and authoritative report JSON
use locked, fsync-backed per-file atomic replacement. Report Markdown and
indexes are projections that `reconcile_report_bus` can rebuild from validated
records. Related files are not one crash-atomic transaction. Lifecycle hook
telemetry and model metrics are observational and must never block task
execution; their durability is intentionally weaker.

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

## Project-local runtime state

Production orchestration is fail-closed and binds every control-plane call to
the same absolute `project_root`. Before any project action or worker dispatch,
activation, classification, initialization, and status must confirm that the
ledger is exactly `${project_root}/.codex/cortex`. An absent MCP server,
init/status failure, unwritable or mismatched root, a set `CORTEX_ROOT`, or
`/tmp` fallback ends the workflow with a blocker; ordinary/unledgered subagent
work is not a substitute. The MCP server rejects `CORTEX_ROOT` outright.
`CORTEX_PROJECT_ROOT` is server-launch compatibility input, not a replacement
for the required per-call `project_root`.

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
delegation is evaluated independently from task kind and risk; Luna handles
lightweight work at low or moderate risk regardless of task complexity. Terra
handles all other non-security work. Security task kind, the security gate, and the
`security_auditor` profile always select Sol, normalizing contradictory task
kinds to security. A non-security Sol exception must be structurally auditable: a supported
extreme criterion with an audit reference, or a ledger-validated failed Terra
attempt. The supported auditable-extreme criteria are
`irreversible_multi_system_recovery`, `safety_critical_incident_response`,
and `novel_cross_system_failure_without_bounded_rollback`. Reasoning effort is
otherwise selected independently of routing; `none` normalizes to `low` and
Sol is at least `high`.

## Scoped worker report bus

Workers publish a strict eight-field `cortex/report/v1` payload through
`record_report`. The canonical sanitized JSON record is task- and attempt-bound;
submission ids make retries idempotent. A receipt links one report to one C2/C3
evidence record and is consumed once. Its `reports/consumptions/` tombstone is
irreversible and prevents replay even if reconciliation repairs derived files.
The task index exposes metadata only.
Delegation indexes separate reports owned by an attempt from report bodies
explicitly granted as context. This keeps cross-worker context intentional and
bounded while acknowledging that local principal/thread values are
caller-asserted, not host identity attestation.
