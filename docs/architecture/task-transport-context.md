# Task transport context

## Root defect

Final15 completed the full orchestration pipeline but emitted one rejected
`read_task` request after documentation publication. The request omitted its
task locator even though the same coordinator connection had already opened
and used exactly one task. Requiring the model to retranscribe the same opaque
locator on every task-scoped call left task identity partly model-owned.

The correction is a connection-scoped identity boundary. It does not infer a
task from project history, task recency, a filesystem path, or a model prompt.

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

