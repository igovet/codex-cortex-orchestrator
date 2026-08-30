# Worker identity boundary (run3k forensic finding)

Development-only analysis. This file is not a runtime contract and must not be
referenced by skills, prompts, profiles, or installed-plugin documentation.

## Final8 follow-up-turn correction

The final8 live event stream exposed a distinct lifecycle error after the
host-mediated consume architecture had been implemented. `SubagentStart`
successfully consumed the pending dispatch and anchored the implementation
worker, but that worker ended its first model turn without publishing. The
coordinator continued the same native agent. Codex preserved `agent_id` and
the parent `session_id`, changed `turn_id`, and did not emit a second
`SubagentStart`. The activation hook had keyed child authority by
`session_id + turn_id` and treated `SubagentStop` as terminal, so the next
worker MCP operation was incorrectly denied as `route_not_anchored`.

The corrected identity and lifetime model is:

```text
server dispatch receipt
        -> SubagentStart consumes receipt once
        -> parent session + native agent identity owns worker lease
        -> zero or more model turns / SubagentStop observations
        -> successful terminal publication closes worker lease
```

`turn_id` is an observation boundary, not assignment identity. A stable
native-agent lease is keyed by the hashed parent-session/agent pair. A
same-turn alias exists only for supported hook payloads which omit `agent_id`;
that alias is removed at `SubagentStop`. The stable lease survives and permits
follow-up turns without another consume. A successful plan, result, or
documentation publication removes both the stable lease and current alias.
After that boundary, later operations under the former agent identity fail
closed.

This also corrects event attribution. Guard observations resolve the stable
child lease before assigning a role, so a worker denial can no longer be
journaled as a coordinator denial merely because its `turn_id` changed.

Acceptance evidence must include a native worker that stops before terminal
publication, receives a coordinator follow-up, resumes under the same
`agent_id` without another `SubagentStart`, and publishes successfully on its
first MCP attempt. Unit tests alone are insufficient; the exact sequence must
pass in ordinary Codex live-dev.

## Finding

The live raw diagnostic stream proves that the first implementation-worker
bootstrap call was made with a reconstructed argument shape (`anchor` and
`bootstrap_token`). The advertised operation rejected it; the worker then
copied the server-rendered names and succeeded. This is not a reliable
first-call contract. The worker had already received an opaque encrypted
native dispatch message, but the MCP server received only the JSON-RPC tool
arguments.

The current stdio composition root has no authenticated caller/session
metadata. The MCP configuration forwards only process environment values,
and the public MCP request contains no Codex session ID, native agent ID,
parent/child relation, or native tool-use ID. The lifecycle hooks do receive
those values, but they run in separate processes and cannot mutate the
already-running MCP server's environment or attach metadata to its JSON-RPC
stream. The lifecycle observer explicitly records this relation as
unavailable/ambiguous. Consequently a hook-written `session -> assignment`
file cannot be safely resolved by the MCP process: the MCP process has no
authenticated session key with which to select that row.

## Security conclusion

Removing the bootstrap token from the public request and resolving the only
minted capability by `assignment_ref` would make the first call easier, but it
would authorize any participant that knows that locator. It would violate the
worker-only and cross-worker isolation guarantees. A hook-side mapping would
have the same defect unless the host supplies an authenticated binding key to
the MCP transport.

Therefore the requested server-resolved worker identity cannot be implemented
securely inside the current MCP stdio boundary by changing the schema or the
skill. The hard architectural defect is the missing authenticated caller
identity/connection binding between native worker creation and its MCP
connection.

## Required architecture before removing the opaque bootstrap input

The host integration must provide one of these authenticated boundaries:

1. **Per-session MCP launch binding (preferred):** when the host creates a
   native worker, it launches that worker's MCP server with a fresh,
   owner-only, one-time binding secret and a server-verifiable child-session
   identifier. The binding is created atomically with the dispatch lease and
   is scoped to the exact child session. The MCP server resolves the pending
   assignment from its authenticated connection identity; the model supplies
   only the assignment locator (or, ideally, no locator when the connection
   has exactly one pending assignment). The server consumes the same pending
   row exactly once and rejects every other connection.

2. **Authenticated MCP initialize metadata:** the host includes a signed
   native-session identity in MCP initialization/connection metadata, and the
   server verifies it against a durable dispatch-binding table. This requires
   host support for connection metadata; ordinary tool JSON arguments are not
   a substitute.

3. **Host-mediated consume operation:** the native host consumes the pending
   dispatch itself and injects the resulting scoped continuation into the
   worker MCP session through an authenticated channel. This preserves the
   public worker tool schema but moves the trust boundary into the host.

The binding table must contain task, assignment, contract revision, dispatch
digest, candidate/catalogue digests, child-session identity, creation and
expiry, and consumed state. It must enforce a unique active binding per
assignment/revision, atomic consume, expiry/reconciliation, and exact replay
of the same continuation. A second worker, coordinator, stale session, or
ambiguous transport must fail closed; it must never mint a replacement
binding automatically.

## Gate

Until the host exposes one of the authenticated boundaries above, retain the
current opaque bootstrap capability in the public contract and classify any
first-call reconstruction as a live failure. Do not weaken isolation merely
to hide the validation error. The run3k successful retry is evidence of
schema recoverability, not evidence that worker identity is solved.
