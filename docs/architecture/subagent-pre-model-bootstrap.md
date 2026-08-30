# Native worker pre-model bootstrap

This development note records the lifecycle boundary used by the current
candidate. It is not an installed runtime contract and is intentionally kept
under `docs/architecture/` only.

## Invariant

The worker must not choose project tools or worker-owned semantic operations
until server-owned assignment evidence has been consumed. The model is not
asked to supply a bootstrap capability. The `SubagentStart` hook consumes the
same assignment locator that the server rendered in the private dispatch
receipt, through the normal domain facade. The store remains the authority for
provenance, capability consumption, continuation creation, and replay
behavior.

## Lifecycle sequence

```text
open_assignment success
        |
        v
private parent-session lease
        |
        v
SubagentStart
  |-- exactly one active lease -> server consume (atomic)
  |                              -> child anchored + bounded context
  |-- zero or multiple leases  -> child remains unanchored (fail closed)
        |
        v
first worker model turn
```

The `PreToolUse` hook claims one pending receipt for each authorized native
spawn. When the host exposes `tool_use_id`, that stable id is retained only as
a digest in the private receipt; older payloads are accepted only when there
is exactly one pending receipt. `SubagentStart` consumes the next claimed
receipt from a lock-protected parent-session queue. It does not use task-name
guessing, host message reconstruction, or a model-supplied secret. Parent and
per-receipt locks prevent concurrent lifecycle events from consuming one
assignment twice or racing the queue. A successful result is recorded as
`consumed` and `authoritative`; the child state is then anchored and receives
only bounded, sanitized assignment context.

## Host boundary and limitation

The observed Codex lifecycle payload exposes the shared parent session, child
turn, and agent id, but does not echo the spawn `tool_use_id` or expose an
authenticated assignment id on `SubagentStart`. The ordered queue is therefore
serialized by the parent session: host start order must match authorized spawn
claim order. Missing claims fail closed, and a second unclaimed native call
with no stable `tool_use_id` is rejected rather than guessed. This is a safe
current-host boundary, but it is not a substitute for a future host-provided
authenticated spawn-to-child correlation field. The hook does not claim that
the opaque host message itself proves assignment content; the backend consume
result is the end-to-end authority.

## Regression coverage

The activation-hook fixtures cover successful pre-model consumption, anchored
first worker project access, repeated `SubagentStart` without a second consume,
missing correlation, and ambiguous active leases. Existing worker bootstrap
guards remain in force when the automatic boundary cannot prove a unique lease;
coordinator routing and its ordinary pre-anchor behavior are unchanged.
