# Public decision-tool architecture recommendation

This is a development-only decision analysis. It is not a runtime contract and
must not be referenced from skills, prompts, profiles, or installed-plugin
documentation.

## Evidence and constraints

The live first-call failure is architectural evidence: a model received a
unified `record_decision` tool whose flat request admitted both clarification
fields and steering fields, then supplied a steering-capable request against a
clarification binding. The backend correctly rejected the family mismatch, but
the first call was already wrong.

The relevant constraints are:

- one MCP server;
- all public tools must survive Codex 0.151 admission;
- arguments must be derived from tools/list schemas and descriptions only;
- no MCP parameter recipes in skills or prompts;
- bindings and replay identity remain server-owned;
- clarification, plan approve/revise/cancel, steering add/retire, supersession,
  idempotency, and pending-decision gates must remain intact;
- no legacy compatibility aliases;
- deeply nested steering schemas have been silently dropped by the host, while
  the current shallow 13-tool catalogue was admitted.

## Alternatives

### A. Unified superset (`record_decision`, 13 tools)

Approximate shape:

```text
record_decision(
  task_ref,
  binding_ref,
  response={
    response_original,
    user_language,
    outcome?,
    supersedes_decision_ref?,
    add?,
    retire_item_refs?
  }
)
```

Advantages:

- smallest catalogue;
- one server-owned binding consumer;
- no sibling operation selection;
- flat collections avoid the known dropped nested steering shape.

Defect:

- the schema is a semantic superset. The model can legally compose fields that
  are syntactically valid but belong to a different binding family;
- family correctness is deferred to backend validation;
- the first-call failure is therefore structurally possible by design;
- descriptions can explain the relation but cannot enforce a binding-dependent
  conditional schema in the static MCP tools/list contract.

Making `outcome`, `add`, or `retire_item_refs` required or optional does not
solve the problem: clarification needs none of them, plan review needs
`outcome`, and steering needs at least one steering collection. Static JSON
Schema conditionals would express this, but the known Codex host admission
boundary is not reliable for complex conditional/deep schemas.

### B. Three narrow family operations (15 tools)

Replace the unified public consumer with exactly three family-specific tools:

```text
record_clarification(
  task_ref,
  binding_ref,
  response_original,
  user_language
)

record_plan_review(
  task_ref,
  binding_ref,
  response_original,
  user_language,
  outcome
)

record_steering(
  task_ref,
  binding_ref,
  response_original,
  user_language,
  add?,
  retire_item_refs?,
  supersedes_decision_ref?
)
```

The catalogue becomes 15 tools: the current 13, minus `record_decision`, plus
the three narrow consumers.

Each input schema is shallow and closed. Steering keeps `add` and
`retire_item_refs` at the operation's top level; it does not use a nested
`steering_delta`, `anyOf`, or `oneOf`. The server still normalizes those fields
to its existing internal delta before persistence.

Advantages:

- the first-call request shape is family-specific and has no cross-family
  optional bag;
- clarification cannot syntactically contain plan outcome or steering
  collections;
- plan review cannot syntactically contain steering collections;
- steering's only variable semantic part is the two shallow collections;
- each operation can have a precise required set and a small host-admissible
  schema;
- binding-family mismatch remains a backend safety check, not the normal way to
  discover the request shape.

Risks:

- the model must select one of three record tools;
- a wrong sibling selection can still happen if the open result does not make
  its next operation unambiguous;
- this is mitigated by making `next_action` an exact enum in every open result,
  making the matching operation's description state that it alone consumes the
  returned binding family, and having the backend reject cross-family bindings
  without mutation;
- catalogue size increases from 13 to 15, but the known host admitted the
  earlier 15-tool surface and the failure was caused by ambiguous nested/input
  shape rather than catalogue count.

### C. Dynamic per-binding schema or MCP capability negotiation

Possible ideas include a server-issued schema after `open_*`, MCP elicitation,
or a continuation capability whose next tool schema is dynamically selected.

These are not suitable as the primary contract here:

- MCP tools/list is a static discovery surface; a normal MCP server cannot
  replace a tool's schema per binding and expect Codex to refresh its callable
  registry synchronously;
- elicitation is host capability-dependent and is not a substitute for a
  deterministic semantic mutation tool;
- a continuation token can authorize the mutation but cannot teach the host a
  new first-call argument shape;
- introducing a second transport or host callback would violate the one-MCP
  constraint and increase, rather than reduce, admission risk.

## Invalid-state comparison

| Invalid state | Unified superset | Three narrow tools |
|---|---|---|
| Clarification with plan outcome | Schema accepts; backend rejects | Schema rejects or wrong-tool call is rejected before mutation |
| Clarification with steering fields | Schema accepts; backend rejects | Schema rejects or wrong-tool call is rejected before mutation |
| Plan review without outcome | Backend rejects after call | Required-field schema rejects before mutation |
| Steering with no add/retire operation | Schema may accept empty arrays; backend rejects | Backend rejects semantic emptiness; shape remains shallow |
| Steering nested wrapper invented by model | Closed schema rejects, if host preserves it | Closed schema rejects; no wrapper is advertised |
| Wrong sibling against binding | Backend rejects | Backend rejects precisely and without mutation |
| Replay of exact accepted response | Server replays | Server replays through the same family aggregate |
| New response after consumed binding | Server stale/conflict error | Server stale/conflict error |

## Recommendation

Use alternative B: three narrow family-specific public record operations, with
the existing internal aggregate methods retained and the existing server-owned
binding as the only authority for family resolution.

This is the smallest architecture that removes the demonstrated invalid
first-call state from the advertised contract without depending on unsupported
conditional schemas or model instructions. It preserves all functionality;
only the public operation boundary becomes family-specific.

Required safeguards for B:

1. Every `open_*` result advertises exactly one enum-valued `next_action` and
   one binding handle.
2. Each record operation has a closed shallow schema with no generic envelope,
   no compatibility alias, and no unadvertised bookkeeping field.
3. `record_steering` advertises only top-level `add` and
   `retire_item_refs`; the backend converts them to the existing internal
   `steering_delta` atomically.
4. Backend binding-family checks remain mandatory even though the schema is
   narrow; they protect against stale, cross-task, malicious, or manually
   composed calls.
5. Real stdio tests must execute clarification, plan approve/revise/cancel,
   and steering add/retire through their matching operations, plus wrong-family,
   empty-steering, replay, stale, supersession, and concurrency cases.
6. The host-admission test must assert all 15 tools are present, each shallow
   schema is visible, and no retired storage names or nested steering wrapper
   appears in the model-facing catalogue.

The recommendation does not claim that tool count alone guarantees model
correctness. It moves the most common family mismatch from an accepted
syntactic superset into an explicit operation-selection boundary while keeping
the server as the final authority.
