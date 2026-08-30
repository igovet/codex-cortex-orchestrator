# Run2t planner handoff forensic matrix

Sanitized, read-only development evidence. No opaque identifiers, prompt
bodies, report prose, or raw payloads are included.

| Boundary | Evidence | Conclusion |
|---|---|---|
| `open_assignment` MCP result | `dispatch_brief` present; rendered-message length/digest recorded; full planner catalogue present | Server renderer produced a complete typed brief |
| Assignment scope | Planner-only `planning_items` and bootstrap capability present | Scope was available before native handoff |
| Native `SubagentStart` hook event | Contains lifecycle/session/process metadata but no prompt, context, rendered-message, or scope field | Host hook stream cannot prove what message was projected |
| Child initial context | No canonical prompt/context payload is exposed in the captured hook event | No evidence of MCP truncation can be established from hook data |
| Child failed publication | Evidence shape varied; failed request lacked reliable preservation of the server catalogue | Failure occurs after handoff, at child evidence construction |
| Child context compaction | No bounded field links the child turn to the renderer digest | Cannot distinguish host mapping loss from child-side context loss using current observation contract |

## Determination

The server output was complete. The first observable host boundary is the native
`SubagentStart` event, and it does not carry the projected message or scope
digest. Therefore the trace cannot attribute disappearance to MCP projection,
coordinator field selection, host spawn mapping, or child compaction. The
definitive production defect is the absence of a verifiable handoff correlation
at that boundary: a child can start without an observable proof that it received
the exact server-rendered dispatch brief.

Run2p/run2r show the downstream symptom: the planner either reconstructed item
references or omitted coverage after the trusted-scope correction. That is
consistent with lost/insufficient typed scope in the child, but the current hook
payload cannot prove which intermediate layer dropped it.

## Production-layer recommendation

Make the spawn handoff server/host-correlated: retain a short digest and item
count of the exact rendered dispatch brief and emit the same opaque correlation
marker in the native child bootstrap event. The child must consume the exact
server-rendered brief before publication. This adds provenance without exposing
prompt or handle contents and makes projection, host mapping, and compaction
loss distinguishable in future runs.
