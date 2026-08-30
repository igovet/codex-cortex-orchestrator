# Run2u worker bootstrap forensic matrix

Sanitized read-only comparison of planner and implementation native-worker
handoffs. No opaque capabilities, prompts, report text, or identifiers are
included.

| Boundary | Planner child | Implementation child | Finding |
|---|---|---|---|
| Server assignment result | Dispatch brief present; bootstrap capability present | Dispatch brief present; bootstrap capability present | Server assignment output was complete for both |
| Assignment locator in server result | Present in structured dispatch result | Present in structured dispatch result | Locator was not absent at MCP boundary |
| Native start event | Lifecycle metadata only; no prompt/context fields | Same lifecycle metadata only; no prompt/context fields | Host event does not expose spawn argument mapping |
| First consume request | Exactly the advertised two-property shape | First request also used the advertised two-property shape | Initial token/locator delivery was structurally plausible |
| Subsequent implementation attempts | Not applicable after success | Added obsolete `capability` and/or `task_ref` properties; one attempt combined them with the advertised fields | Child/model retried with wrong property mapping rather than preserving the original call |
| Successful consume result | Present for planner | No successful result observed for implementation | Failure occurred before server bootstrap consumption |

## Determination

The implementation child did not fail because the server omitted its bootstrap
capability or assignment locator. Both were present in the coordinator-visible
assignment result. The current native-start hook payload exposes no prompt or
spawn-argument field, so it cannot prove whether the coordinator projected the
wrong result member or whether the child subsequently rewrote the call after
context loss.

The decisive observable difference is the implementation retry sequence: after
the correctly shaped two-property attempt, the child issued calls containing
unsupported legacy properties (`capability` and/or `task_ref`). This is a
property-mapping/retry defect, not a missing server token. The planner used the
exact advertised bootstrap property shape and consumed successfully.

## Production-layer recommendation

Keep the closed advertised bootstrap schema strict. Make the native spawn
handoff carry an opaque dispatch correlation and an immutable server-rendered
bootstrap brief, then require the child to submit that brief’s exact property
shape once. Emit sanitized child-bootstrap attempt/result categories so a
wrong-property retry is distinguishable from token absence without exposing
capabilities or prompts.
