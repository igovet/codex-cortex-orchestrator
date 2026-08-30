# Run3d duplicate planner forensic matrix

Sanitized read-only evidence from the isolated diagnostic stream. No task
handles, opaque capabilities, prompt bodies, or report content are recorded.

| Boundary | First planner dispatch | Replacement planner dispatch | Finding |
|---|---|---|---|
| `open_assignment` result | Successful non-replayed mutation | Successful parent-linked mutation | No transport/tool ambiguity existed |
| Server dispatch brief | Rendered message present; native dispatch projection present | Rendered message present; native dispatch projection present | Server issued an authoritative host projection |
| Native projection shape | Closed projection contains assignment binding, digest, isolated-history marker, message, and task name | Same shape | Projection was available without reconstruction |
| `SubagentStart` observation | Lifecycle metadata only; no delivered message/digest fields | Lifecycle metadata only | Current hook stream cannot prove native argument delivery |
| Coordinator action | Interrupted first worker as “non-authoritative” | Created replacement immediately | Replacement was not justified by an ambiguous result |

## Root cause

The first server dispatch was authoritative and successful, including the
closed native dispatch projection. The coordinator nevertheless treated the
handoff as non-authoritative because the host boundary supplied no observable
proof that the exact `native_dispatch` projection had been delivered to the
child. This is an architectural provenance gap at the coordinator/host spawn
boundary, not a failed `open_assignment` call.

Because the first mutation had succeeded and there was no ambiguous transport
failure, interrupting it and creating a replacement was an unexplained
duplicate planner assignment.

## Minimal production correction

Require the coordinator to use the server-returned `native_dispatch` projection
unchanged and persist its opaque dispatch digest as the child bootstrap
correlation. A successful `open_assignment` plus a recorded host dispatch
attempt must not authorize replacement solely because the child-start event
lacks message fields. Replacement requires explicit ambiguous transport or a
closed, server-reported stale/conflict state.
