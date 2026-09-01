# Lifecycle telemetry

<!-- GENERATED:START -->

Cortex 1.14.9 ships two bounded native hook components: an activation guard and
a sanitized lifecycle observer. The activation guard applies only after an
explicit Cortex route selection. It validates task-anchoring order and
correlates a native worker dispatch with a one-shot server receipt without
rewriting the native spawn call. The unchanged host call remains the sole
plaintext delivery of the exact server-rendered bootstrap. `SubagentStart`
binds the real child audience with owner-only digests and does not repeat the
message as additional context.
The server projection always carries `fork_turns`, `task_name`, and the exact
`reasoning_effort` before its potentially long `message`; `model` is present
for Terra or Sol and omitted for default Luna. The hook rejects a missing or
different required routing discriminator and the retired host-level `role`
field. This keeps routing validation aligned with the live host schema while
leaving the server-rendered bootstrap authoritative.
Each coordinator session owns a private receipt directory and an atomic active
index whose receipt state progresses through `pending`, `delivery_pending`,
`worker_bound`, and `server_claimed`. Add, claim, bind, server-claim, and
consume transitions are owner-only and lock-protected; terminal assignment
consumption removes the receipt from the active index immediately. Hook
processes locate this directory through `PLUGIN_DATA`; the plugin MCP process
resolves the same exact installed-package data directory through `CODEX_HOME`
when `PLUGIN_DATA` is not in its environment. Historical or foreign-session
files, content-hash filename order, and timestamps never participate in
routing. Settled diagnostics are separately capped at the last 64 consumed
receipts per session; that cleanup is not required for correct active lookup.

The observer records structural session and subagent start/stop markers plus
the package build identity. These records let live verification prove that a
real child started, initialized Cortex MCP, consumed assignment evidence, and
published one terminal outcome. Sanitized assignment reads and publications
are attributed as `role=worker` and `scope=assignment`; coordinator events
remain coordinator-scoped. Sensitive message bodies, task or worker locators,
handles, worker reports, and raw host payloads are not lifecycle telemetry.

Lifecycle markers are not ledger authority, authorization, or completion
evidence. The MCP backend still owns task state, assignment evidence,
idempotency, publication, reconciliation, and closure. A spawn result or a
`SubagentStart` marker alone never substitutes for the worker's successful
server-side evidence consumption and terminal publication.
After an explicit successful assignment result, a host dispatch denial is not
an ambiguous MCP outcome and never authorizes reopening or replacing that
assignment. The pending receipt is retained for diagnosis and the route stops
until the host boundary is corrected.

The active feature registry is [features/index.md](../index.md). See the
[orchestration ledger](../orchestration-ledger/index.md) and
[advisory governance](../advisory-governance/index.md) for the V12 contract.

<!-- GENERATED:END -->
