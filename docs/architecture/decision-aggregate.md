# Decision aggregate

Status: Phase D implementation on the `1.12.1` branch.

The decision boundary is a server-owned aggregate. The coordinator supplies
the user intent and the exact response; it does not manufacture binding
identity, prompt context, subject digests, or approval relations. The existing
V12 tables remain readable historical evidence while new semantic decision
commands use the transactional command-receipt path.

## State model

```text
absent -> pending(binding_ref, logical_identity)
pending -> consumed(decision_ref, response_digest)
pending -> stale(contract revision or subject changed)
consumed -> replayed(same binding and response)
consumed -> conflict(same binding and different response)
```

Each family `open_*` operation computes a logical identity from the task,
typed subject, prompt digest, and effective contract revision. The revision,
logical slot, unique binding row, and command receipt are derived under one
SQLite `BEGIN IMMEDIATE` transaction. Re-opening the same intent returns the
same binding; it never creates a recovery binding.

The matching family `record_*` operation resolves the binding, validates its
immutable subject and revision, writes the decision, consumes the binding, and
records the result receipt in the same transaction. Steering receipt identity
contains the complete delta and any superseded decision relation. A lost
response is therefore reconciled by a read-only lookup or an exact replay. A
different semantic input is a conflict, not a second decision.

## Capability preservation

The aggregate preserves clarification, immutable plan approval, request
revision/cancellation, steering and supersession, subject/revision staleness,
same-live-worker relation metadata, arbitrary user language and byte-exact
responses. Worker scheduling remains host/coordinator policy; the aggregate
records validated intent and never chooses a worker or authors a question.

Approval decisions consume the server-issued approval binding and its immutable
plan/view digest and source sequence. Callers do not reconstruct those values.
Historical V12 bindings and decisions remain readable and are not rewritten.

## Transaction boundary

```text
MCP call
  -> semantic registry admission
  -> BEGIN IMMEDIATE
       resolve binding/context
       enforce logical slot and revision
       mutate binding + decision
       insert command receipt
     COMMIT
  -> structured result
```

The legacy storage mutation ledger remains for legacy internal operations. The
semantic decision facade uses `DecisionAggregate` and the v16 command receipt
store. Read-only task reconciliation creates no command receipt and cannot
open a new binding.

## Qualification matrix

| Invariant | Required evidence |
| --- | --- |
| Same logical open | Repeated identical opens return byte-identical `binding_ref`. |
| Atomic record | Binding consumption, decision row, and receipt commit together. |
| Exact replay | Same response returns the original decision and `replayed=true`. |
| Conflict | Changed response is rejected without a new decision. |
| Lost response | Read-only reconciliation finds `consumed`; no new binding is issued. |
| Isolation | A binding from another project is not resolvable. |
| Historical compatibility | Existing V12 rows remain readable after migration. |
| Feature parity | Clarification, approval, revision/cancel, steering, supersession, stale detection and worker relation metadata remain represented in the registry/parity matrix. |
