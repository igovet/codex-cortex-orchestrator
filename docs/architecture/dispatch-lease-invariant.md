# Dispatch lease invariant

This development note records the assignment-ownership boundary introduced
for the live orchestration stabilization.

An assignment that successfully mints a server-owned worker bootstrap owns one
active dispatch lease. The lease is represented by the capability row: a
`minted` row is active, `consumed` means the worker claimed the dispatch, and
`stale`/`conflict` are terminal server states. Parent-linked replacement is
rejected while the parent lease is active; the host cannot create a second
owner merely because a native-subagent correlation event is absent.

The lease has a bounded five-minute expiry. A replacement request first reads
the authoritative capability row in the same SQLite admission transaction. If
the row is consumed, replacement is allowed. If it is still minted and within
the lease, the request fails with the active-lease conflict and creates no
delegation. If it is minted but expired, the server records the explicit stale
transition and only then permits the replacement. Worker consumption also
fails closed after expiry and records the same stale transition.

The invariant is deliberately server-side and independent of prompt wording,
SubagentStart telemetry, or host reconstruction. Existing outcome-ownership
history remains intact; the lease only controls whether a parent-linked
replacement may be created before the original dispatch is reconciled.
