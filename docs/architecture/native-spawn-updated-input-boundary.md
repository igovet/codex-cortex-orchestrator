# Native spawn `updatedInput` boundary

Status: causal boundary proven on Codex `0.151.0`; source fix implemented and
source suites passed. A later 0.151.0 live run also exposed and corrected a
separate spawn-envelope compatibility defect described below. This document is
development evidence only.

## Controlled host probes

| Probe | Native input | PreToolUse rewrite | Spawn result | Child start | Control marker | Safe outcome |
|---|---|---:|---:|---:|---:|---|
| A | short valid message | no | success | observed | terminal `PROBE_A_OK` | pass |
| B | exact server-rendered message | no | success | observed | child model turn and terminal completion | pass |
| C | byte-identical server-rendered message | yes | success | observed | no MCP `server_ready` | encrypted function-output decode failure |

B and C used the same server projection digest. The server message was valid
UTF-8, a plain string, below the observed native host limit, and accepted by
the advertised spawn schema. The C child stopped after lifecycle registration
but before its MCP initialization and assignment-evidence consumption.

## Proven boundary

`PreToolUse.updatedInput` must not be used for a native spawn call. Codex owns
the function call and encrypted transport linkage; replacing the input after
model generation can leave the function output associated with an envelope
the child cannot decrypt or decode. A successful spawn tool response therefore
does not prove a usable child when this rewrite is present.

## Correct ownership

```text
open_assignment
    -> server records bounded assignment and dispatch-correlation digests
    -> model requests native spawn using the advertised host schema
    -> PreToolUse validates and atomically claims the receipt
       (context-only response; no permission override; no updatedInput)
    -> Codex accepts the unchanged native function call
    -> SubagentStart binds the exact child audience using digest-only state
    -> worker MCP server_ready
    -> worker consumes assignment evidence as its first semantic action
    -> worker publishes one terminal outcome
```

PreToolUse remains a correlation and replay guard, not a native transport
adapter. The host-owned spawn message is the sole locator delivery;
SubagentStart binds the child audience without storing or repeating plaintext.

## Codex 0.151.0 routing-envelope correction

The current `collaboration.spawn_agent` host input may carry an optional atomic
`model` and `reasoning_effort` pair beside the server projection. It does not
carry the older host-level `role` field. The activation hook previously
required all three older metadata fields whenever any metadata was present, so
the real current-host pair was rejected as `dispatch_mismatch` before a worker
could start. The coordinator then incorrectly reconciled the already-successful
assignment mutation, producing an unexplained replay.

The hook now accepts either no host routing metadata or the complete current
model/effort pair, rejects either value alone and rejects the retired role
field. A dispatch mismatch response explicitly states that the assignment is
already committed and must not be reopened or replaced. The advertised
assignment contract carries the same no-replay rule for every explicit success.
The MCP server remains the only authority for assignment evidence and terminal
publication.

## Acceptance gates

- no `updatedInput` or `permissionDecision: allow` in successful native-spawn
  PreToolUse output;
- no task locator, worker locator, native message, or assignment body is stored
  in the lifecycle receipt; only the exact digest-bound child may consume the
  one-shot dispatch claim;
- spawn success, SubagentStart, child MCP `server_ready`, first successful
  assignment-evidence consumption, one terminal publication, coordinator
  reconciliation, and successful closure are all observed;
- no hook failure, tool validation error, decrypt/decode error, mutation
  replay, coordinator replacement, or worker follow-up is accepted.
