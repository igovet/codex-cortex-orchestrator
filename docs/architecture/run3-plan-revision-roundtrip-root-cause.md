# Plan revision report round-trip root cause

## Failed live observation

The replacement planner consumed the first immutable plan successfully, then
its first `publish_plan` attempt failed schema validation. The replacement had
preserved `order` and `dependencies` in every stage. Those fields were not
invented by the worker: the server had added them while canonicalizing the
first publication and returned them in the consumed report body.

## Architectural invariant

For every immutable semantic report body:

```text
advertised write shape -> canonical stored shape -> consumed read shape
                                  |
                                  +-> must satisfy the advertised write shape
```

A server-owned normalization field cannot be present in consumed immutable
evidence while being rejected by the corresponding publication schema. Report
handoffs are exact evidence transfer, so a replacement worker must not need to
guess which server-returned properties to delete.

## Resolution

- The plan-stage schema accepts the complete canonical server-returned stage
  shape on the first call.
- The bookkeeping fields remain optional and server-owned.
- Both common caller index conventions are admitted because caller-supplied
  bookkeeping is non-authoritative; rejecting zero-based values caused a
  separate live first-call failure even though the store discards them.
- Array order remains authoritative; the store recomputes stage order and
  predecessor bookkeeping before hashing and persistence.
- A staged stdio MCP regression now publishes a first plan, requests a
  revision, consumes that exact immutable plan body in a replacement planner,
  and republishes the consumed body without schema repair.

This preserves exact report handoff and one canonical storage identity while
closing the write/read/write contract.

## Live result status

The live run that exposed this defect is failed and cannot be promoted by a
successful retry. A fresh content-addressed candidate and a new task are
required for acceptance.

## Completed-binding replay defect

A later fresh run recorded a revision decision successfully, then reopened the
same logical plan review. The command-receipt replay returned the original
pre-consumption snapshot (`consumed=false`) even though the ledger binding had
already been consumed. The coordinator consequently attempted to record the
same user intent again, and a harmless textual normalization difference made
the second call conflict.

Decision-open command receipts now retain identity but refresh the exact
binding's current ledger projection on replay. A completed binding returns
`consumed=true` plus its existing compact decision reference. The advertised
tool description directs the caller to continue from that decision and never
record the binding again. This reconciliation is shared by all decision
families rather than patched in the plan-review facade.

## Closure evidence ownership

A long live run later reached closure and the first `close_task` call omitted
the old opaque evidence object. Requiring the coordinator to reconstruct
closure evidence contradicted the server-owned aggregate: the backend already
owns the effective contract, report manifests, decisions, consumption receipts,
coverage, and conformance review. The public close command therefore no longer
advertises caller-authored evidence. It derives and persists a bounded snapshot
of current task state, while the caller retains only the advisory verdict and
optional risk/follow-up annotations.

## Clean live qualification

Fresh run 6 completed with 44 sanitized events, no failures, and no mutation
replays. The replacement planner was parent-linked to the first planner; the
second plan superseded the first; implementation consumed the revised plan;
verification consumed the implementation report. All worker consumption
receipts carried the exact immutable manifest digest and complete chunk set.
Both plan revisions remained immutable and the current projection matched only
the revised plan. Closure succeeded on its first call with server-derived
evidence and an explicit ready-with-risks verdict for unavailable live browser
checks.
