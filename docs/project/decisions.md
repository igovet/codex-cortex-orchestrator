# Architecture decisions

<!-- GENERATED:START -->

## Hybrid ownership, deterministic integrity

The coordinator chooses intent, decomposition, specialist/model/effort,
parallelism among ready nodes and interpretation of evidence. A planner worker
owns the project solution plan. The backend stores typed graphs and validates
transitions; it is not a scheduler or semantic planner.

Correctness-critical dependencies, artifact generations, revision validity,
worker correlation, terminal kind and user-decision binding are machine-checked.
They cannot depend on a model remembering prose after steering or compaction.
This replaces the competing initiative/prose-pipeline design, not supplements it.

The complete contract and acceptance gates are in
[Typed orchestration integrity](typed-orchestration-integrity.md).

## Coordinator and worker separation

Explicit activation selects the orchestration route; ordinary complexity does
not. A fresh coordinator opens the complete task before project execution and
records governance before assignment. Its direct project access is limited to
the bundled skill's exact known-path knowledge-routing exception.

Workers perform project inspection, implementation, commands, tests and
documentation. A fresh worker consumes its server-issued assignment before work.
That assignment contains the graph-derived scope, checks, artifact procedure
and fixed terminal kind. Product outcome context preserves requirements but
does not duplicate or expand that worker's assignment.

The native dispatch is a compact exact bootstrap, not a second instruction
contract. The coordinator forwards it unchanged once after a non-replayed
assignment. It never authors a worker publication.

## Static catalogue and host-bound identity

The installed catalogue contains twenty operations. Initial Desktop MCP
connections may have no trusted child identity and therefore begin neutral,
without actor authority. Supported lifecycle/call hooks correlate the exact
native dispatch and child. Terminal assignment consumption commits worker role.

A retained initial catalogue does not bypass server-side actor checks. No
mid-turn refresh is required; an explicit later worker catalogue read can show
only worker operations. Hooks cannot asynchronously schedule or stop agents.
The coordinator uses supported native spawn, wait, list and interruption calls.

## Declared readiness and artifact barriers

Initial baseline observation precedes artifact-dependent discovery and planning.
Nontrivial planning produces a candidate graph that needs independent semantic
validation. A structurally valid graph can still miss product requirements or
declare false independence.

The graph has explicit dependencies, capabilities, contribution ownership,
verification subjects, execution modes and finite remediation policies.
Assignment admission rechecks current readiness atomically. An implementation
audit waits for acceptable implementation evidence; an idle native slot does
not make it ready.

One shared-checkout mutator is admitted at a time. Compatible ready read-only
auditors may run in parallel. This is artifact exclusion, not a one-worker
workaround. A mutator seals a successor generation; earlier dependent evidence
remains historical until verified against the appropriate generation.

The backend compares worker-observed fingerprints and does not inspect the
product filesystem. Unavailable observations are not assumed matches.
Reconciliation waits for native quiescence and preserves actual stopped-worker
effects as evidence rather than inventing rollback or authorship.

## One terminal kind and canonical verification

Planning nodes publish plans, documentation nodes publish documentation
assessments, and other nodes publish results. Profile labels and edit counts
cannot change this kind. A single cross-kind terminal slot prevents competing
terminal reports for one assignment.

Observed node coverage is the only verification narrative. Plans declare
expected checks; artifact commitments are separately measured integrity data.
Required empty risk/unresolved arrays remain explicit. No omitted evidence is
silently defaulted to success. The public description's required-property list
is generated from the authoritative schema rather than maintained separately.

Reports are immutable. Native commentary is permitted during work, but no
legacy progress/synthesis publication API or chunk-assembly continuation exists.

## Steering, recovery and convergence

Direct user changes commit immediately, atomically advance the contract and
invalidate prior authority. The coordinator interrupts affected protected
native tasks, observes quiescence, reconciles artifacts and resumes current
work. Old snapshots remain immutable. A racing publication can return a clean
non-publication state, never current evidence or permission for a corrective
retry.

Recovery starts with current state and any required active-continuation read.
Chronology is a separate audit route. Silence, elapsed time or a single stop hook
cannot establish loss. Complete supported native observations and absence of a
finalized report are necessary before a lineage-linked replacement.

In-contract defects use validated bounded remediation with independent
regression. A successful repair resolves current capabilities without rewriting
the failed report. No-progress and exhausted budgets remain honest incomplete
evidence. Scope, authority or material risk changes do not silently authorize
ordinary remediation.

## Adaptive review and mandatory closure review

Complete ordinary low-risk plans continue without a question. Material risk,
external authority, genuine product alternatives and explicitly requested
review require a decision-ready packet. Complexity, incomplete evidence or an
internal recoverable failure alone does not require approval.

When alternatives can be responsibly specified and independently validated,
one user answer selects and approves one complete branch atomically. Otherwise
a genuine pre-plan choice is opened first. Direct user-authored steering is
already the answer; it needs no second confirmation.

Approval fulfills its current decision boundary. Authorized replanning does
not automatically repeat it. Changed material authority/risk or an explicit
renewed review request creates a fresh boundary.

After verification, the coordinator presents the result, documentation impact,
risks, unrun checks and verified links, then asks to revise or close. Only the
current direct close choice permits closure. Graph/generation/evidence gates
still apply; user consent cannot turn incomplete evidence into a ready verdict.

## Exact model transport

Luna is the default for most work, with effort up to max. Its native model
override is omitted so the configured default is used. Terra is reserved for
genuinely complex planning/architecture, and Sol for rare very-high-risk
security work. Both carry explicit overrides. Every worker has explicit effort
no higher than max; ultra is never valid.

The backend validates the selected pair without escalation or substitution.
Profiles are packaged assignment policy, not unsupported native agent types or
degraded ad-hoc fallbacks.

## Current-only storage and verified human views

SQLite schema v2 is created directly. Unsupported layouts are rejected without
migration, legacy sentinels or alternate identity routes. Initiative storage
and governance services are removed. Historical installations remain untouched.

Each canonical transition, event and server-derived receipt commits in one
transaction. Identical ambiguous-response reconciliation preserves that commit;
changed payloads conflict. Idempotency never proves native effects occurred.

Only plans and finalized reports have user-facing Markdown. Rendering,
containment, regular-file, freshness and digest/readback checks precede a link.
One transient projection I/O failure can be repaired inside the original
publication using the durable report and exact expected bytes. Persistent
errors, unsafe paths and external edits are not bypassed. A later exact receipt
reconciliation repairs the view, never publishes a second report.

Maintenance remains an explicit non-MCP operator route. Backups cover the
whole shard; restore is offline and requires independently established
quiescence and exact confirmations. Neither maintenance nor projections write
ledger data into the product project.

## Documentation and qualification

Documentation mutations precede final verification. Final checks and read-only
documentation-impact evidence bind the same latest generation. A no-impact
claim needs worker evidence, not a manufactured documentation edit.

Source tests, isolated source MCP, native CLI and real Desktop are distinct
evidence classes. All-tools/all-profiles qualification must exercise meaningful
ready work and actual conditional preconditions, not call tools for a checklist.
The final CLI/Desktop pair uses one unchanged stamped payload.

## References

- [Orchestration ledger](../features/orchestration-ledger/index.md)
- [Advisory governance](../features/advisory-governance/index.md)
- [Human-readable task views](../features/human-readable-task-views/index.md)
- [Storage classification](storage-classification.md)
- [Security policy](../../SECURITY.md)
- [Verification](verification.md)

<!-- GENERATED:END -->
