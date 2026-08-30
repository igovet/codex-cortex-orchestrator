# ADR-001: Cortex 1.12.1 stabilization architecture

- Status: Proposed for implementation
- Date: 2026-08-29
- Scope: Cortex runtime and isolated candidate delivery

## Decision

Cortex 1.12.1 will be stabilized around one server-owned domain kernel. The
model chooses a domain intent through MCP; it does not own storage assembly,
state transitions, retry identity, evidence wiring, or lifecycle invariants.

The kernel is the only production layer allowed to decide whether a command
may transition state. MCP adapters, projections, skills, prompts, and tests
must derive their contracts from the same declarative registry and must not
reimplement those rules.

The architecture has these boundaries:

```text
LLM intent
  -> semantic MCP command/query boundary
  -> domain kernel and transaction
  -> append-only ledger + read projections
  -> typed command receipt and result
```

## Invariants

### Commands and receipts

Every semantic command has a server-derived logical slot and request digest.
The state transition and its durable result receipt commit atomically. An
identical call replays the stored result; a different payload for the same
slot returns a conflict; an incomplete call writes nothing. A lost response
therefore cannot cause a second mutation or require a caller-generated
idempotency key.

### Decisions

The logical decision identity is derived from task, subject, decision type,
prompt digest, and contract revision. One identity has at most one pending
binding. Re-opening an unchanged decision returns that binding (or its
consumed decision). Recording atomically consumes it. An identical response
replays; a changed response conflicts; a stale revision reports stale state.
Recovery is read-only reconciliation and never creates a replacement binding.

### Assignments and evidence

An assignment contains an immutable contract snapshot and server-computed
predecessor evidence. Workers consume typed evidence capabilities; they do
not submit arbitrary report or decision references as a substitute for the
snapshot. Evidence consumption is recorded transactionally, and publication
is unavailable until all declared predecessor evidence is consumed.

### Publications

Plan, implementation/verification result, and documentation are distinct
typed publication commands. Chunking is an internal storage concern, not a
model-facing protocol. A publication validates contract coverage, evidence,
observations, deviations, risks, unresolved items, and documentation impact
before any durable publication row is created. An invalid payload has zero
writes. A valid logical slot has one terminal publication; rework is created
only after a material contract/evidence change or an explicitly accepted
result is shown to be wrong.

### Governance and closure

Governance is a material-change projection, not a progress counter. Routine
worker stages and rework attempts do not create initiative revisions. An
initiative exists only for a cross-task objective, durable dependency, or
material risk. Closure is a server-side aggregate check over accepted plan,
completed assignments, coverage, verification, documentation impact, pending
decisions, blockers, and contract revision.

### Typed capabilities

Handles are typed server capabilities (`task_ref`, decision binding, decision,
assignment, publication, and evidence cursor). Each producer/consumer edge is
declared once and tested by passing the exact returned value unchanged. A
capability cannot be reconstructed from UI text or exchanged between
projects/tasks.

## Declarative contract registry

One registry generates the public tool catalogue, input/output schemas,
handler bindings, producer/consumer capability matrix, error taxonomy, and
conformance tests. Tool argument names and shapes remain exclusively in the
advertised schemas and tool/property descriptions; skills and prompts contain
only task semantics. Divergent hand-maintained copies are a build failure.

The public surface is CQRS-shaped: semantic commands perform one transition;
queries read projections. Evidence consumption is the sole query-like
operation that also records a bounded read receipt. Storage budgets, chunk
indexes, and physical report assembly are never public arguments.

## Candidate provenance

`cortex-dev` must build an immutable content-addressed candidate in the
isolated `.cortex-dev/.codex` environment. Before starting Codex it computes
canonical source and candidate manifests, compares every runtime-critical
file, and fails before launch on any mismatch. The runtime publishes product
version `1.12.1`, candidate build ID, and verified source/candidate parity.
Receipts retain the build ID so a live result is attributable to exact code.
The stable host profile is never modified.

The database schema is independent of product version. Migrations remain
forward-only and preserve existing data; no downgrade or reset is implied by
the product version change.

## Cutover sequence

1. Inventory and quarantine the failed live evidence; do not reinterpret it as
   a passing source test.
2. Implement and qualify content-addressed candidate delivery and a black-box
   stdio MCP harness that imports no checkout modules.
3. Build the registry and command-receipt foundation.
4. Complete the decision vertical slice, including replay, conflict, stale,
   concurrency, cross-project, and lost-response-after-commit tests.
5. Complete assignment/evidence capabilities and publication atomicity.
6. Add governance and aggregate closure checks.
7. Run the full multi-turn live scenario only after all slices pass: one
   clarification, one plan approval, planner, implementation, independent
   verification, documentation impact, and closure.

## Acceptance

The release is not accepted until source/candidate parity is proven before
live launch; all eleven public tools are visible from one MCP server; every
producer/consumer capability edge passes unchanged; incomplete publications
leave zero writes; lost responses replay one receipt; one unchanged decision
has one binding; routine progress causes no initiative churn; no predictable
rework occurs; and three fresh isolated projects complete the live scenario
with zero tool errors, unexplained mutation replays, hidden worker errors, or
stale-candidate ambiguity. Product/plugin/server version is `1.12.1`; the
database schema remains independently versioned and forward-only.

