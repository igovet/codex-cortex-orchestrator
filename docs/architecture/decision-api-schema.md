# Decision API schema architecture

Status: current development architecture for the 1.12.1 cutover. The former
unified recommendation and the family proposal retained below are historical
and superseded; they are not API or implementation instructions.

This document defines the public decision boundary that preserves the complete
orchestration feature set while making invalid decision combinations
unrepresentable in the advertised MCP schemas. The current narrow design is
described first; the earlier family proposal is retained only for comparison.

## Current narrow architecture

The selected public boundary is a 15-tool catalogue. Clarification, plan
review, and steering each have one narrow open operation and one matching
record operation: `open_clarification`/`record_clarification`,
`open_plan_review`/`record_plan_review`, and `open_steering`/`record_steering`.
Each record operation consumes only its matching server-issued binding; no
generic record operation derives a family from caller input.

The server owns binding identity, immutable approval relations, exact replay,
stale detection, and atomic receipts. Host admission uses family-specific
closed contracts, while steering collections remain flat to survive host
conversion. Every assignment receives an immutable capability snapshot, so a
mid-work steering decision produces a new snapshot for later assignments and
cannot rewrite an active worker's authority.

The remaining nine tools are the task, assignment, publication, governance,
and closure operations named in the parity matrix. `open_plan_review` delivers
the verified approval view and Markdown link; the coordinator presents that
server-rendered evidence and records the user's answer through the matching
record operation. Candidate provenance remains content-addressed under server
version `1.12.1`. Live smoke is transport-only and LLM-driven, and revoked
observation leases remain readable historically without retaining claim
authority.

## Superseded unified architecture (historical)

The public boundary is one server-owned decision lifecycle, not six public
family commands. The 13-tool catalogue is:

`open_task`, `read_task`, `open_clarification`, `open_plan_review`,
`record_decision`, `open_steering`, `open_assignment`,
`consume_assignment_evidence`, `publish_plan`, `publish_result`,
`publish_documentation`, `assess_governance`, and `close_task`.

Clarification, plan review, and steering are distinguished by the opaque
binding issued by the server. `record_decision` consumes that binding and the
server derives the decision family and legal transition. The coordinator never
reconstructs a binding, approval relation, effective-contract revision, or
assignment capability from prose, labels, digests, or a rendered view.

Steering uses flat server-owned collections for additions, retirements, and
supersession. This is an admission constraint: host/tool conversion must not
depend on nested family-specific objects that are liable to be dropped or
rewritten at the host boundary. Every assignment receives an immutable
capability snapshot. Mid-work steering creates a new effective-contract
revision and snapshot for subsequent assignments; it cannot mutate the
capability snapshot already issued to a worker.

Plan approval is delivered only from a verified `open_plan_review` result. The
coordinator presents the exact server-rendered approval view and its verified
Markdown link, then records the user's answer against the same server-owned
binding. The link is presentation evidence, not a caller-created authority
token.

All mutations use the same server-owned atomic receipt/replay boundary. An
ambiguous transport result is reconciled read-only against the original
binding; it never opens a replacement decision. Candidate provenance is
content-addressed and remains on server version `1.12.1`, while the isolated
live launcher proves candidate/source identity before the ordinary Codex
session starts. `cortex-live-smoke` is transport-only: the LLM reads the real
tmux pane and structured observations and owns clarification, approval,
steering, acceptance, and retry decisions.

Historical observation leases remain readable after session stop in read-only
mode. Revocation removes claim authority but does not erase the bounded
historical event stream; a fresh start must reject stale ownership and issue a
new generation.

## Superseded family-specific proposal (historical)

Use three typed decision families, each with an open operation and a matching
record operation:

| Family | Open command | Record command | Purpose |
| --- | --- | --- | --- |
| Clarification | `open_clarification` | `record_clarification` | Ask one user question whose answer supplies task context. |
| Plan review | `open_plan_review` | `record_plan_review` | Bind one immutable finalized plan/view and accept `approve`, `request_revision`, or `cancel`. |
| Steering | `open_steering` | `record_steering` | Bind the current task contract and record a user-authorized effective-contract delta, including supersession of prior steering. |

The three record commands are deliberately separate. They are not aliases for
one overloaded command with a discriminator. Each command has one closed input
schema, one state machine, and one result type. This makes invalid states such
as a clarification carrying a plan approval handle, a plan review carrying a
steering delta, or a steering response targeting a report impossible at the
MCP boundary.

The public decision surface therefore has six operations, not an arbitrary
fifteen-operation semantic catalogue. Existing task, assignment, evidence, publication,
governance, and closure operations remain separate capabilities. The decision
family count must be derived from the feature model and registry, never used as
a compatibility target.

## Why the overloaded pair is rejected

The family-specific `open_clarification`/`record_clarification`,
`open_plan_review`/`record_plan_review`, and `open_steering`/`record_steering`
operations are transactionally safer than the historical storage API. The old
overloaded pair's discriminator created a large
conditional state space:

```text
subject_type × decision_type × subject_digest × approval relation
                     × steering delta × supersession relation
```

The server can reject bad combinations after receiving them, but the advertised
schema invites the model to construct combinations that should not exist. It
also makes the same binding/reflection fields serve unrelated protocols. That
is the source of repeated schema repairs and rework, not merely a missing
validator branch.

Separate operations reduce each schema to the legal state space. They also
give the registry a one-to-one mapping between a feature, a domain command,
its typed binding, and its result receipt. The domain kernel remains the final
authority: schema separation is an admission guarantee, not permission for the
coordinator or model to interpret responses.

## Operation contracts

The exact field names and shapes are to be generated from the registry into the
advertised schemas. This document intentionally describes semantics, not a
copy of the MCP argument contract; skills and prompts must not repeat fields,
enums, limits, or payload examples.

### Clarification

`open_clarification` accepts coordinator-authored question intent and creates
or replays one server-owned pending clarification binding. Its identity is
derived from the anchored task, question subject, prompt digest, and effective
contract revision. An unchanged open returns the same pending binding or the
already-consumed decision projection.

`record_clarification` accepts only that exact binding capability and the exact
user response. The server obtains prompt, subject, task, and revision from the
binding. It atomically consumes the binding, writes the decision and command
receipt, and returns the durable decision reference. The backend does not
translate, summarize, infer consent, or decide what the response means.

### Plan review

`open_plan_review` accepts coordinator intent to present a finalized plan. The
server resolves the plan publication and creates or replays a ready review
binding containing the immutable plan digest, approval-view digest, source
sequence, and task/assignment relation. Those values are server-owned and are
not reconstructed by the coordinator.

`record_plan_review` accepts only the exact review binding and one of the three
semantic outcomes:

```text
approve | request_revision | cancel
```

The outcome is a user assertion, not an authorization decision made by the
backend. The server verifies that the binding is still current, atomically
records the response, consumes the review binding, and returns a receipt. An
unchanged response replays the original receipt; a different response for the
same binding is a conflict. `request_revision` and `cancel` remain bound to
the same immutable plan/view, so they cannot accidentally refer to a newer
plan.

### Steering and supersession

`open_steering` binds the current task contract revision and the coordinator's
question about a proposed change. It creates one pending steering binding for
that logical subject. The server supplies the current effective-contract
snapshot and the set of eligible prior steering decisions.

`record_steering` accepts the exact binding, the user response, and the
coordinator-authored delta intent. The schema permits only the steering delta
shape for this operation. The kernel validates that the delta is non-empty,
targets the anchored task, and references the bound effective revision. On
success it atomically writes the user decision, creates the next effective
contract revision, records any explicit supersession relation, consumes the
binding, and stores the command receipt. A replay cannot create another
revision; a changed response or delta conflicts with the original receipt.

Supersession is a relation between durable steering decisions. It is not a
generic recovery mechanism and it does not create a new binding for an
unchanged question. A new binding is permitted only when the logical subject,
prompt, or effective contract revision materially changes.

## State machines

All six operations use the same server-owned transaction pattern but distinct
typed aggregates:

```text
open_*:
  absent -> pending(binding_ref, logical_identity)
  pending -> pending (same identity, exact same binding_ref)
  consumed -> consumed (same identity, existing decision projection)

record_*:
  pending -> consumed(decision_ref, response_digest)
  consumed + identical response/intent -> replayed(existing receipt)
  consumed + changed response/intent -> conflict(no write)
  pending with changed contract/subject -> stale(no write)
```

The transition and its command receipt commit in one database transaction.
There is no state in which a decision is durable but its binding consumption or
receipt is absent. If the transport loses a response after commit, the
coordinator performs read-only reconciliation with the original binding; it
does not open a replacement decision.

## Producer-consumer capability matrix

Handles are scalar, typed, server-issued capabilities. Every edge below is
tested by passing the exact structured result value byte-for-byte; no value is
copied from rendered text or reconstructed from a digest.

| Producer | Produced capability | Consumer | Allowed result |
| --- | --- | --- | --- |
| `open_clarification` | clarification binding | `record_clarification` | exactly one consume or replay |
| `open_plan_review` | plan-review binding | `record_plan_review` | approve, request-revision, cancel, replay, conflict, or stale |
| `open_steering` | steering binding | `record_steering` | one effective-contract revision or replay |
| `record_clarification` | decision reference | assignment/evidence/closure projections | immutable decision evidence |
| `record_plan_review` | decision reference | planner/closure projections | immutable approval/revision/cancel evidence |
| `record_steering` | decision reference and effective-contract revision | assignment/planner/verification projections | new contract snapshot and supersession evidence |

No record command accepts another family's binding. A plan-review binding is
not a clarification binding, and a decision reference is not a binding. The
registry must reject missing, extra, or cross-family capability edges at build
time.

### Historical compatibility decision (superseded)

The following paragraph records the rejected family-API migration advice. It
must not be applied to the selected narrow architecture, where the three
family-specific record operations are canonical.
is the canonical public record operation.

The historical proposal said not to retain a public `open_decision` or
generic record alias merely to preserve an old name. That advice belonged to
the rejected family-specific design and is superseded by the unified contract.

Historical V12 decision rows remain readable as evidence. A migration adapter
may translate historical rows into the read projection, but it must not expose
the old callable argument shape. If an exact operation name is retained during
an incremental cutover, it must have exactly one family-specific, closed
contract; otherwise it is not a compatibility layer but a second protocol.

## Compact catalogue and output contracts

Each family returns a compact output containing the typed binding or decision
capability, state, and receipt metadata needed for the next operation. The
full explanatory context remains in structured content, but storage internals,
chunk indexes, caller budgets, reconstructed digests, and unrelated family
fields are excluded from the callable contract. Optional `outputSchema` data
is retained in compact form because it tells the model which typed result it
received; catalogue size is controlled by removing redundant fields, not by
removing the result contract.

All input schemas are concrete closed objects. Conditional legality is encoded
by operation separation, not a large top-level discriminator union. The
registry generates the schemas, handler map, capability edges, safe-error
taxonomy, and first-call/replay qualification cases from one source.

## Feature-preservation checklist

The split preserves the existing orchestration behavior:

| Existing capability | New decision family |
| --- | --- |
| Genuine user clarification and arbitrary original-language response | Clarification |
| Immutable plan approval relation | Plan review |
| Explicit plan revision request | Plan review |
| Plan cancellation | Plan review |
| Same-task steering and effective-contract revision | Steering |
| Steering supersession relation | Steering |
| Server-owned binding identity and exact replay | All families |
| Stale subject/contract detection | All families |
| Lost-response reconciliation | All families |
| Worker/assignment/closure decision evidence | Decision references from all families |

The coordinator still owns question wording, user interaction, orchestration
ordering, DAG adaptation, worker selection, governance judgment, and rework
decisions. The backend owns only identity, binding, validation of immutable
relations, atomic state transitions, and durable receipts. No orchestration
functionality is removed by the schema split.

## Qualification gates

Before replacing the current pair, the candidate must pass black-box stdio
qualification from the exact isolated candidate package:

1. First open and first record for each family succeed from `tools/list` alone.
2. Identical opens converge on one scalar binding.
3. Identical records replay one receipt.
4. Changed response/intent conflicts without a second decision or revision.
5. Plan review accepts only the three stated outcomes and the exact ready view.
6. Steering creates one effective-contract revision and preserves supersession.
7. A lost response after commit reconciles through the original binding.
8. Cross-family and cross-project handles fail closed.
9. Incomplete requests write zero durable rows.
10. The compact single-MCP catalogue remains within the tested model-context cap.

Only after these gates pass should the real tmux LLM-driven lifecycle be run.
