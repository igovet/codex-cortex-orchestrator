# Task transport context

## Root defect

Final15 completed the full orchestration pipeline but emitted one rejected
`read_task` request after documentation publication. The request omitted its
task locator even though the same coordinator connection had already opened
and used exactly one task. Requiring the model to retranscribe the same opaque
locator on every task-scoped call left task identity partly model-owned.

The coordinator correction is a connection-scoped identity boundary. It does
not infer a task from project history, task recency, a filesystem path, or a
model prompt. Worker authority uses a stricter host-audience and
same-connection boundary described below.

## State machine

```text
new stdio connection (unbound)
  |
  +-- open_task success ------------------------+
  |                                             |
  +-- exact task_ref on successful scoped call  |
                                                v
                                  bound exact task context
                                     |                 |
                                     | omitted locator |
                                     +-----------------+
                                     | inject bound ref
                                     |
                                     +-- exact successful selector
                                         replaces binding

process restart -> unbound; no ledger-recency fallback
```

## Isolation matrix

| Situation | Resolution | Required result |
| --- | --- | --- |
| Same coordinator connection after `open_task` | Use the exact task reference returned by that successful opening | A later task-scoped operation may omit the locator |
| Fresh or restarted MCP process | No context exists | Omission fails before dispatch; the server never chooses the newest or only ledger task |
| Fresh process with exact task locator | Resolve through the existing compact-locator guard | Success establishes the new connection context |
| Intentional task switch in one process | Resolve the supplied exact locator first | Replace context only after successful resolution/output admission |
| Invalid, ambiguous, or cross-project locator | Existing store resolver rejects it | Do not change the current connection context |
| Two simultaneous Codex sessions | Each owns a different stdio process and local binding | No shared mutable task-context state |
| Native worker MCP process | Separate process and connection | Cannot inherit the coordinator's in-memory task binding |

## Worker publication audience boundary

A worker's first assignment read remains mandatory. The host must first bind
that exact child to the server-issued dispatch using one owner-private,
digest-only lifecycle receipt. Assignment authority is consumed page by page
and publication becomes available only on the terminal page. The same MCP
connection retains the role and assignment binding; a new process or connection
cannot recover it from any public or durable locator.

| Worker publication situation | Required result |
| --- | --- |
| Exact same connection after assignment consumption | Use the existing binding and unchanged atomic publication path |
| Fresh or restarted connection after exact assignment consumption | Fail closed; copied locator and durable continuation are not bearer authority |
| Assignment was never consumed | Fail closed without consuming the minted capability |
| Partial or different connection binding | Fail closed without overwriting any connection state |
| Foreign/malformed worker locator, provenance or dispatch drift, or durable stale state | Fail closed without publication |
| Task steering after consumption | Retain the assignment's immutable consumed revision; do not substitute the task's latest revision |
| Confirmed lost native worker | Record explicit blocked/aborted evidence and create an atomically linked successor; never recover the old worker authority |

## Public contract rule

Task-scoped tools retain the compact task locator as an accepted explicit
selector. It is not mandatory after an exact task has been bound on the same
connection. Tool descriptions and schemas are the only model-facing source of
this contract; skills and workload prompts contain no parameter guidance.

## Verification obligations

- Real stdio: task opening followed by a locator-free task read succeeds.
- Real stdio restart: a locator-free read fails despite an existing ledger
  task; an exact selector succeeds and enables the next locator-free read.
- Two candidate stdio processes concurrently open different tasks and each
  locator-free read returns only its own task.
- Explicit invalid or foreign locators do not mutate the active binding.
- Event observations use the resolved task locator so bounded diagnostics
  remain task-anchored even when the original request omitted it.
- Real persistent source stdio worker: terminally consume and publish on the
  original process; prove a second initialized process cannot read or publish
  from the copied worker locator and creates no report operation.
- Role tests prove a coordinator connection cannot switch to worker audience,
  while the exact host-bound worker connection can consume the assignment.
- Loss tests prove explicit blocked/aborted evidence and successor lineage are
  atomic and that timeout or lease expiry alone remains non-authoritative.
