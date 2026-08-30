# Decision-response architecture (development note)

## Decision

Keep one public `record_decision` operation and make the binding the server-owned
family discriminator. A clarification response may carry a contract delta; the
server applies that delta in the same decision transaction, consumes the binding
once, and advances the clarification hold only after the complete transaction
commits. Plan-review responses accept only their outcome, approval relation, and
common response fields. Steering responses accept the contract delta and optional
supersession relation.

## Alternatives considered

### A — One binding-driven superset operation (selected)

This keeps the proven 13-entry catalogue, preserves one opaque binding and one
idempotency/reconciliation slot, and lets the server resolve the family before
interpreting family-specific fields. The important semantic rule is that every
advertised field has a defined behavior for each family: common response fields
work for all families, a clarification delta is an atomic contract amendment,
plan-review fields remain restricted to plan review, and steering fields remain
restricted to steering. Invalid combinations fail after server binding
resolution without consuming the binding.

### B — Split family-specific record operations

This makes invalid combinations easier to represent in schemas, but adds public
tools to an already admission-sensitive catalogue and duplicates binding,
replay, conflict, and host-discovery behavior. It also makes the operation name
part of model routing even though the binding already carries that authority.
The additional catalogue surface is a material regression for the proven live
host limit and is not justified by the clarification delta requirement.

### C — Dynamically constrained response capability

MCP tool schemas are advertised at catalogue/list time; this repository has no
supported per-binding dynamic tool-schema capability in the Codex host. A
binding-specific schema would therefore either be stale, unavailable to the
model, or require a second discovery protocol. It cannot provide a reliable
first-call contract.

## Invariants

- The binding, not caller-selected family data, determines semantics.
- Clarification response plus contract delta is one durable decision and one
  effective-contract revision, or neither is committed.
- Exact replay returns the original receipt; a changed response or delta is a
  conflict and never mints a second decision.
- Plan-review and steering continue to retain their existing approval,
  supersession, and stale-binding rules.
- No MCP request shape or parameter recipe is placed in skills or prompts.

## Assignment snapshot ownership

Publication completeness is evaluated against the immutable effective-contract
revision captured by the assignment's worker capability. A later steering
revision never retroactively adds requirements to an in-flight assignment.
Those items remain visibly unowned/uncovered until a subsequent assignment
explicitly receives them. This preserves first-publication authority while
allowing the worker to voluntarily implement newly observed user intent.
