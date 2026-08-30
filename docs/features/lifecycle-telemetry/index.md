# Lifecycle telemetry

<!-- GENERATED:START -->

Cortex 1.12.2 ships two bounded native hook components: an activation guard and
a sanitized lifecycle observer. The activation guard applies only after an
explicit Cortex route selection. It validates task-anchoring order and
correlates a native worker dispatch with a one-shot server receipt without
rewriting the native spawn call. At `SubagentStart`, it supplies the
server-owned worker context as host additional context.
Each coordinator session owns a private receipt directory and an atomic active
index containing only `pending`, `delivery_pending`, and `worker_bound`
entries. Add, claim, bind, and consume transitions share that session's lock;
consumption removes the receipt from the active index immediately. Historical
or foreign-session files, content-hash filename order, and timestamps never
participate in routing. Settled diagnostics are separately capped at the last
64 consumed receipts per session; that cleanup is not required for correct
active lookup.

The observer records structural session and subagent start/stop markers plus
the package build identity. These records let live verification prove that a
real child started, initialized Cortex MCP, consumed assignment evidence, and
published one terminal outcome. Sensitive message bodies, handles, worker
reports, and raw host payloads are not lifecycle telemetry.

Lifecycle markers are not ledger authority, authorization, or completion
evidence. The MCP backend still owns task state, assignment evidence,
idempotency, publication, reconciliation, and closure. A spawn result or a
`SubagentStart` marker alone never substitutes for the worker's successful
server-side evidence consumption and terminal publication.

The active feature registry is [features/index.md](../index.md). See the
[orchestration ledger](../orchestration-ledger/index.md) and
[advisory governance](../advisory-governance/index.md) for the V12 contract.

<!-- GENERATED:END -->
