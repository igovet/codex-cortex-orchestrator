# Project overview

## Purpose

Cortex 1.15.6 is an explicitly activated Codex plugin for typed, durable
multi-agent coordination. Runtime authority lives under
[plugins/cortex](../../plugins/cortex/). Repository scripts, tests and documents
support development; they are not an installed orchestration contract.

The implementation contract and outstanding work are tracked in
[Typed orchestration integrity](typed-orchestration-integrity.md).
Its Completion checklist distinguishes implementation, source/wire checks,
native CLI and actual Desktop qualification.

## Responsibility split

The coordinator chooses intent, decomposition, profiles, model/effort,
ready-node parallelism, evidence interpretation, bounded remediation and user
communication. Workers own project investigation, planning, implementation,
commands, tests and documentation. The backend persists typed graphs,
revisions, receipts, actor bindings, reports and decisions, and rejects invalid
transitions. It never chooses or spawns the next worker.

The coordinator's project-read exception is the bundled skill's exact
knowledge-routing allowlist. Missing paths and substantive source discovery
remain worker work; mentioning project state does not expand coordinator
authority. A normal repository coding task does not automatically activate
Cortex.

## Execution integrity

The graph records semantic nodes, dependencies, required/provided capabilities,
contributions, verified outcomes, checks, mutation domains and finite repair
policies. Current readiness is a computed projection, not a prose stage list.

Baseline observations precede artifact-dependent discovery/planning. Planned
delivery requires an independently validated current graph and any required
review. Audits of implementation wait for acceptable terminal predecessor
evidence on the current artifact generation. Native capacity and readiness
are separate: free slots cannot make an unready node executable.

Shared-checkout writes are serialized by the mutation gate. Compatible
read-only audits may run in parallel; artifact-independent work is distinct.
No native worktree isolation is assumed.

Each successful new assignment returns an exact native dispatch. Its child
first consumes one typed assignment authority, including node scope, expected
checks, execution mode, artifact procedure and terminal kind. Complete scoped
product requirements are separate context, not a second assignment contract.
The child uses the assigned publication kind and one cross-kind terminal slot.

Observed node coverage is canonical verification evidence. Artifact observations
identify the checked generation without duplicating that narrative. Every
result supplies observations or explicit null only for artifact-independent
work. Workers keep hash-only comparison manifests in owner-private system-temp
scratch, separated by user, Codex home and project; the ledger never reads
project files or grants workers write access to its private database tree.

## Decisions, recovery and closure

Complete low-risk minimal/light plans are informational unless the user
requested review or a genuine material decision requires it. High-risk/material
plans and external authority branches require the exact current decision packet.
An approval binds the current plan and verified human view, not future revisions.

Direct semantic steering is already authorization for its concrete change.
It atomically revises the contract, invalidates affected authority and preserves
historical evidence. The coordinator observes/interrupts stale native work as
required, establishes quiescence and reconciles the actual artifact boundary.
It does not ask for a second confirmation of the same change.

Recovery reads current state and, when delegated work is unfinished, active
continuations. Timeline is for a real chronology need, not worker recovery.
Timeout and silence do not prove loss. Finite in-contract repair and independent
verification continue without manufactured permission questions.

Before closure, present the verified result, documentation impact, risks and
unrun checks. Open the mandatory current revise/close review and record the
direct choice. Only close permits closure; earlier plan approval is insufficient.
A revision keeps the same task.

## Runtime and storage

- Python 3.11+, standard-library runtime; supported hosts are Linux and macOS.
- Codex Plugins and native multi-agent support.
- Luna through the configured default (no native model override), explicit
  effort no higher than max. Terra is for genuinely complex architecture or
  planning; Sol is rare and security-risk-specific. Ultra is prohibited.
- Twenty audience-authorized MCP tools. Their live advertised schemas alone
  define call arguments; skills and workload prompts do not duplicate shapes.
- Current SQLite schema **2**, created directly per canonical project root.
  Old/unknown storage is rejected without migration or compatibility fallback.
- Canonical state is host-private; no database, report or projection is written
  inside the target project. Derived Markdown links are returned only after
  current digest verification.
- Atomic mutation receipts are identity-bound, not authorization. Identical
  ambiguous reconciliation preserves a receipt; unexplained successful replay
  is not a valid workflow.
- Maintenance is a separate explicitly invoked CLI; restore is offline.

## Main source surfaces

- [MCP facade](../../plugins/cortex/scripts/cortex.py)
- [Advertised contracts](../../plugins/cortex/scripts/cortex_runtime/public_contracts.py)
- [Domain operations](../../plugins/cortex/scripts/cortex_runtime/domain_api.py)
- [Execution graph](../../plugins/cortex/scripts/cortex_runtime/execution_graph.py)
- [Graph ledger](../../plugins/cortex/scripts/cortex_runtime/graph_ledger.py)
- [Typed publication schemas](../../plugins/cortex/scripts/cortex_runtime/typed_publications.py)
- [Private store](../../plugins/cortex/scripts/cortex_runtime/v12_store.py)
- [Worker fingerprint procedure](../../plugins/cortex/scripts/cortex_runtime/artifact_fingerprint.py)
- [Model routing](../../plugins/cortex/scripts/cortex_runtime/model_routing.py)
- [Packaged profiles](../../plugins/cortex/profiles.json)
- [Orchestrator skill](../../plugins/cortex/skills/orchestrator/SKILL.md)
- [Worker control skill](../../plugins/cortex/skills/cortex-control/SKILL.md)

## Development and qualification

Use [README](../../README.md), [conventions](conventions.md) and
[verification](verification.md). The semantic version is frozen at 1.15.6 for
this repair; only its content-addressed cache suffix changes.
`./scripts/cortex-dev` refreshes only the isolated candidate.
Do not synchronize or replace the user's stable plugin.

Qualify local contracts, all profiles, DAG, steering and recovery before a short
real CLI run. Then complete full all-tools/all-profiles CLI and actual Desktop
on the same stamped payload. Any payload edit invalidates both live results.
Current readiness and unrun gates are in
[release readiness](../release-readiness.md), not inferred from source test counts.

## Feature registry

- [Orchestration ledger](../features/orchestration-ledger/index.md)
- [Advisory governance](../features/advisory-governance/index.md)
- [Plugin packaging](../features/plugin-packaging/index.md)
- [Coordinator communication](../features/coordinator-communication/index.md)
- [Knowledge routing](../features/knowledge-route-contract/index.md)
- [Human-readable views](../features/human-readable-task-views/index.md)
- [Lifecycle observation](../features/lifecycle-telemetry/index.md)
- [Operator maintenance](../features/operator-maintenance/index.md)

## Project references

- [Conventions](conventions.md)
- [Architecture decisions](decisions.md)
- [Storage classification](storage-classification.md)
- [Gotchas](gotchas.md)
- [Verification](verification.md)
- [Security policy](../../SECURITY.md)
