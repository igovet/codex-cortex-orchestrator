# Run2y native handoff forensics

## Observed failure

The first planner assignment mutation succeeded. Its raw MCP result contained a
valid server-owned native dispatch projection, including the assignment-bound
digest and the three closed native spawn arguments. No host lifecycle event was
observed for that projection. The coordinator then read task state, declared
the first projection unavailable, opened a parent-linked replacement planner
assignment, and spawned only the replacement. This is a failed run because one
logical planner assignment became two successful mutations without an
ambiguous transport result.

## Measured result shape

- The first native bootstrap message was about 23 KiB.
- The same message appeared twice inside the public assignment result: once as
  semantic rendered text and once inside the callable native arguments.
- The public result also retained bulky legacy delegation/service fields.
- The bootstrap prompt embedded the full effective-contract catalogue even
  though the worker's mandatory first evidence-consumption call returned the
  same authoritative catalogue.
- It also embedded predecessor and decision context that should be delivered by
  the same authoritative consumption boundary.

## Root cause

The architecture treated an LLM-mediated host spawn as a byte transport for a
large duplicated state snapshot. Server-owned identity and digest validation
were correct, but the handoff was not operationally compact. The model had to
retain and reproduce a large nested blob before the worker could perform the
very call that already existed to fetch authoritative state. The recovery path
then converted a presentation/retention failure into a duplicate mutation.

## Required boundary

```text
open_assignment transaction
        |
        +-- durable assignment + capability + dispatch digest
        |
        `-- compact native spawn projection (one message copy)
                    |
                    `-- worker first call: consume_assignment_evidence
                              |
                              +-- full effective scope
                              +-- assignment mission context
                              +-- bounded decision question/answer evidence
                              +-- predecessor reports/manifests and bodies
                              `-- worker continuation
```

The native bootstrap carries only policy/profile material and the exact opaque
identity required to reach the consumption boundary. Full task scope,
predecessor evidence, and decision bodies do not belong in the bootstrap.

## Acceptance matrix

| Property | Required evidence |
| --- | --- |
| One logical assignment | One successful `open_assignment`, followed by one host spawn; no replacement without an ambiguous transport result |
| Compact public result | Closed top-level result with no legacy delegation/service payload |
| One message copy | Native bootstrap text exists only in the callable native arguments |
| Server-owned identity | Assignment reference and dispatch digest validate before host spawn |
| Authoritative context | Full scope, mission, decisions, and predecessors arrive from worker evidence consumption |
| Decision preservation | Product answer is absent from bootstrap and present in the consumed durable decision evidence |
| No prompt-level MCP schema | Bootstrap contains behavioral policy only; live tool schemas define call arguments |
| First-call viability | Real stdio open, unchanged host projection, worker consume, and first publication all succeed without corrective hints |

