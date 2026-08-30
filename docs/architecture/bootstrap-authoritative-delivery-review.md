# Bootstrap-authoritative delivery review

Development-only review record for the proposed redesign in which a native
worker carries only an exact bootstrap capability and assignment locator, then
obtains its authoritative assignment contract from `consume_assignment_evidence`.

## Required invariants

| Boundary | Required invariant | Failure mode |
|---|---|---|
| Capability admission | Capability is an opaque, server-minted scalar bound to project, task, assignment, contract revision, build, candidate, source, catalogue, and dispatch digest | Reject stale, cross-task, cross-assignment, cross-build, cross-candidate, cross-catalogue, and wrong-revision calls without disclosure |
| Session binding | Consumption must bind to the actual child session/turn when the host exposes authenticated lifecycle identity; absence remains unavailable, never inferred from `agent_id` or assignment prose | Fail closed for claimed native identity; permit only explicitly supported same-session reconnect semantics |
| One-shot semantics | First consumption atomically mints one continuation and returns the complete assignment delivery; identical reconnect/retry replays the same delivery without minting a second continuation | No duplicate child delivery or orphan capability |
| Assignment delivery | Returned delivery includes task anchor, assignment anchor, effective revision, profile proof, publication family, exact semantic scope, predecessor manifests, constraints, acceptance context, and continuation | Worker cannot safely publish from an incomplete bootstrap result |
| Scope | Delivery uses one canonical compact-reference projection with unique item refs, category, ordinal, text, and assignment role; planner gets all planning items; owner gets owned items; review/docs get non-owning contributing/evidence items | Reject missing, malformed, duplicate, retired, or cross-task refs |
| Predecessor evidence | Manifests are immutable metadata; body evidence remains available only through the scoped evidence operation and must be consumed before publication | Embedded report instructions never become trusted policy |
| Revision | Effective-contract revision is checked atomically at consumption and publication; a stale continuation cannot publish after contract change | Return capability-stale and require server-owned rework/continuation path |
| Rework | Parent/rework delivery inherits exactly the parent’s active owned scope unless a server-approved transfer changes it; unrelated items cannot enter by fallback | Reject ownership overlap and inconsistent parent relation |
| Public surface | Host message contains only trusted policy plus exact capability/assignment locator and no reconstructed task/scope payload; public schemas expose no routing map or canonical identity | Host prompt forwarding becomes transport-only and cannot alter scope |
| Size/security | Delivery and message are bounded; all untrusted text is sanitized/isolated; no secrets, paths, raw reports, or payload recipes cross the boundary | Reject over-limit or unsafe delivery before mutation |

## Required production-path tests

- Real stdio `tools/call` opens a task and planner assignment, extracts only
  the exact bootstrap/assignment values from the result, consumes bootstrap,
  parses the returned canonical scope, and succeeds on the first `publish_plan`.
- Repeat consumption returns byte-equivalent delivery/continuation and creates
  no second capability or assignment event.
- Wrong assignment, task, build/candidate/source/catalogue digest, revision,
  stale capability, and cross-session claims fail closed.
- Review, security, performance, debugger, QA, and technical-writer profiles
  receive non-owning scope and cannot publish as owners or steal ownership.
- Parent rework receives only inherited active scope; parallel non-overlapping
  ownership remains valid.
- Predecessor manifests remain metadata-only and publication requires the
  scoped evidence receipt.

## Native host adapter qualification

The server currently persists model/reasoning metadata for audit and legacy
selection bookkeeping, but `open_assignment` does not accept a coordinator
selection. Those values therefore are not authoritative routing decisions and
must not be emitted as native spawn arguments. The closed native projection is
limited to the exact server-rendered message, stable task name, and
`fork_turns: none`; the active coordinator/host owns model and reasoning
selection under its policy. Any future server-owned selection must be added as
an explicit, validated assignment decision before it can cross this boundary.

The production-path conformance test feeds the actual stdio structured result
through `validate_native_dispatch_projection`, captures the unchanged
arguments at a fake host spawn seam, and then verifies that the child consumes
the exact bootstrap capability as its first MCP action. Digest mutation,
cross-assignment use, missing fields, and the 64 KiB message bound remain
fail-closed checks.
