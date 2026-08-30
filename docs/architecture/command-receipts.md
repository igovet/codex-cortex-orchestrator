# Transactional command receipts

This document defines the first vertical slice of the Cortex domain-kernel
architecture. It preserves the existing append-only reports, decisions,
timeline, projections, and all historical migrations. The new table is
additive; no historical row is guessed, rewritten, or backfilled.

## Responsibility boundary

The model chooses a semantic command. The domain kernel owns the logical slot,
admission, mutation, and result receipt. SQLite commits the domain mutation and
its receipt in one `BEGIN IMMEDIATE` transaction. The legacy `idempotency`
table remains available for historical protocol paths; migrated commands use
`command_receipts` as their authority.

## Receipt identity

Each receipt is scoped by the store's `project_hash` and has a unique
`logical_slot`. The request is canonicalized and hashed by the server. A
matching slot and digest returns the stored result without invoking the domain
mutation. A matching slot with a different digest returns `command_conflict`.

The receipt records aggregate type/id, command name, status, result envelope,
optional runtime build identity, and creation/completion sequence metadata.
Successful receipts are the only rows written by this slice. Admission or
mutation failure rolls back the whole transaction, leaving no successful
receipt and no partial domain mutation.

## Recovery and concurrency

SQLite serialization makes concurrent identical calls converge on one logical
slot. One caller performs the mutation and receipt insert; later callers replay
the durable envelope. If a response is lost after commit, the same command
request is reconciled by slot and returns the saved envelope. A changed request
cannot overwrite it.

## Migration

Migration `16 / v16-transactional-command-receipts` creates the table and
indexes on both fresh and existing stores. It is forward-only, preserves all
previous migrations and evidence, and is included in maintenance health
validation. Product versioning is independent of SQLite schema version.

## Interface

`V12Store.run_command_receipt(...)` is the tested kernel-facing primitive. Its
mutation callback receives the already-open transaction, so preconditions and
domain writes can share the receipt transaction. `lookup_command_receipt` is a
read-only reconciliation primitive. Public MCP wiring will migrate semantic
commands onto this boundary only after the vertical slice qualification gates
pass.

## Qualification matrix

| Guarantee | Evidence |
| --- | --- |
| Fresh/existing forward migration | `test_v12_compatibility.py`, `test_command_receipts.py` |
| Exact replay without a second mutation | `test_command_receipts.py::test_forward_migration_and_exact_replay` |
| Changed request conflict | `test_command_receipts.py::test_changed_request_is_conflict_and_failed_admission_writes_nothing` |
| Failed admission leaves no receipt | same test |
| Concurrent identical calls converge | `test_command_receipts.py::test_concurrent_identical_commands_have_one_mutation` |
| Historical evidence retained | existing compatibility and timeline suites |

