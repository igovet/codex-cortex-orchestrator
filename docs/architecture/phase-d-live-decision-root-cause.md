# Phase D live clarification root cause

Status: **blocking architectural defect**. This is a read-only analysis of the
focused attached-client live attempt recorded in
[Phase D focused live verification](phase-d-live-verification.md). It did not
rerun live-dev, modify the isolated profile, or inspect raw user/worker logs.

## Scope and evidence boundary

The live attempt used the verified isolated candidate. The candidate's relevant
runtime, public contracts, worker-message renderer, semantic registry, and
bundled orchestration skills are byte-identical to the current checkout. The
findings below therefore describe the code that actually ran; they are not an
old-cache hypothesis.

The retained, sanitized live evidence establishes this order:

1. task opening, advisory assessment, and one worker assignment succeeded;
2. a genuine product clarification appeared in the coordinator-facing pane;
3. the operator supplied the single allowed answer;
4. neither member of the clarification decision pair appeared;
5. no accepted first worker-owned publication event appeared; and
6. the coordinator then remained waiting/working until failure cleanup.

The observation proves that the question was rendered in the coordinator
session, not that a particular worker authored its wording. The MCP server did
not author it: no clarification command was called, and the server contains no
question-rendering or host-message implementation. The available evidence has
no structured worker-origin event, so it cannot honestly distinguish a direct
coordinator question from a coordinator rendering of an unstructured worker
request. Treating either attribution as proven would be fabrication.

## Root cause

The Phase D family API correctly offers a server-owned *decision binding*, but
it does not provide a server/host-owned *clarification hold*. A binding alone
does not establish any of these relations:

- the visible question is the one covered by the binding;
- a particular worker assignment is suspended for that question;
- a user response is a continuation input for that assignment; or
- the host must deliver a recorded response to the original live worker, or
  create a safe parent-linked replacement when that worker cannot continue.

The assignment was opened before the visible question. Its public result does
not advertise a typed assignment/dispatch outcome; its schema is deliberately
generic and accepts an open-ended handle object. The coordinator could therefore
dispatch a worker, render an ordinary question later, and receive ordinary chat
text back without creating any durable causal edge between the worker and that
text. Cortex did not queue the response behind a waiting worker. The response
entered the coordinator chat only. A worker might separately have been waiting,
but the current runtime has no state or host bridge capable of delivering this
untyped answer to it.

This explains the stalled publication: the worker has no valid, advertised
semantic way to learn that the response exists, and the coordinator has no
durable continuation capability that identifies a safe same-worker follow-up.
It therefore cannot produce the worker-owned terminal publication required by
the live gate. Waiting is an expected symptom of an unrepresented dependency,
not evidence that transport failed.

## Catalogue and contract findings

The candidate's `tools/list` catalogue has the intended fifteen semantic
operations, including the three decision families. Its clarification pair
advertises the binding producer/consumer relationship. That is necessary but
not sufficient for a first-call orchestration path:

| Surface | What it currently says | Why it fails live orchestration |
| --- | --- | --- |
| Clarification family descriptions | A clarification binding can be issued and then recorded. | They do not state or represent the required hold-before-visible-question and record-before-worker-continuation invariant. |
| Assignment result contract | A generic handle container is advertised rather than a closed assignment dispatch/hold projection. | A caller cannot discover a typed “waiting for this clarification” relation from the live result alone. |
| Result/publication contracts | Only plan publication has a detailed closed result. Other assignment/publication results reuse a generic output shape. | A worker cannot derive its complete, current publication path and success evidence from the advertised contract alone. |
| Result-schema descriptions | Shared mutable schema construction causes some generic result descriptions to inherit an unrelated closure description. | `tools/list` is not a trustworthy self-explaining guide for assignment/result operations. |
| Semantic registry | It declares decision binding capability edges, but no clarification-hold, suspended-assignment, continuation-delivery, or worker-event capability. | The registry cannot generate the missing state, schema, or tests. |

This is a public-boundary defect, not a reason to put call recipes in prompts.
Tool descriptions may describe semantic purpose and sequencing; only the live
schemas and their property descriptions may describe call arguments.

## Contract/compiler drift

The public catalogue was cut over, while the runtime guidance was not cut over
as one unit.

The orchestrator, control, adaptive-pipeline, context-compaction, documentation
guidance, worker renderer, and several profiles still name retired storage-era
operations and/or their arguments. The worker renderer instructs a worker to
use retired report and evidence operations, then asks it to report a blocked
question through an ordinary report. Those operations are absent from the
fifteen-tool catalogue. The current renderer consequently cannot teach a worker
to use the live semantic publication or clarification path.

This is also a direct breach of the repository rule that MCP argument contracts
must exist only in advertised schemas/descriptions. The necessary correction is
not a renamed field list in skills. The correction is:

1. remove all MCP argument names, shapes, enums, limits, and examples from
   skills, profiles, worker-message text, live prompts, and documentation
   intended as model instructions;
2. retain only semantic operation names, purposes, and ordering invariants in
   those contracts; and
3. make the public catalogue complete and self-describing enough for first-call
   use without external recipes.

The current prompt lint checks selected fixtures but did not prevent this
package-wide source/candidate drift.

## Host continuation and hidden-event findings

The plugin exposes no host follow-up or worker-steering adapter. The semantic
steering family only persists a decision/effective-contract change; no source
surface maps a recorded decision to a non-forgeable live worker handle or calls
the host continuation facility. Bundled prose conditionally says that the host
may continue a worker, but it does not give the host a durable, typed request to
do so. The historical hierarchy design explicitly required a parent-only
question, an exact paused worker, and exact-worker resume; it is marked planned
and has no current implementation.

Likewise, the repository contains policy/tests requiring a bounded sanitized
worker event stream, but no current producer, host capture adapter, or
assignment-scoped event projection exists. `consume_assignment_evidence`
reads declared report evidence; it is not a worker MCP-event stream. The live
run therefore had no acceptable hidden-worker evidence to inspect. This is a
missing capability, not merely an omitted verifier step.

## Single architectural remediation: Clarification Hold aggregate

Introduce one typed **Clarification Hold aggregate** behind the existing
clarification family. It preserves the fifteen-operation public catalogue and
keeps orchestration model-owned; it does not create a backend scheduler.

### Durable model

`open_clarification` atomically creates or replays one logical hold. In
addition to the existing decision binding, the hold owns:

- the canonical question identity and immutable prompt digest;
- the task's effective-contract revision;
- an optional originating assignment;
- a state of pending, answered-delivery-pending, delivered, stale, or
  superseded;
- exactly one response receipt when answered; and
- an opaque, host-only continuation capability for the exact live worker when
  one is known.

The command must be completed before the coordinator renders the corresponding
user question. A worker-originated clarification uses the same command with its
assignment identity; it transitions that assignment to a non-terminal
clarification hold rather than forcing a false terminal report. The server
deduplicates same logical questions and never opens a replacement binding for
ordinary recovery.

`record_clarification` atomically consumes the same hold, stores the exact
response once, emits typed decision evidence, and creates a pending delivery
record. It does not schedule, author a response, or automatically wake a
worker.

### Host and coordinator boundary

The host adapter consumes only the server-issued continuation capability. It
may deliver the recorded decision to the exact still-live worker; otherwise it
returns an explicit unavailable/ambiguous outcome. The coordinator then chooses
the existing safe recovery policy: wait/reconcile when the worker is live, or
create an evidence-linked parent assignment only when same-worker continuation
is unavailable. The backend never chooses a worker, creates a replacement,
authors a question, decides an answer, or advances the DAG.

The coordinator contract becomes semantic and enforceable:

```text
open clarification hold → render one user question → record the observed answer
→ deliver/reconcile the same worker or create evidence-linked replacement →
worker publishes its own outcome
```

This is an ordering invariant, not an argument recipe. The public tool schemas
remain the sole source of parameter names and shapes.

### Event observability

Add a host-authenticated, assignment-scoped sanitized event capture boundary.
It records each worker MCP request/result classification, including the first
publication attempt, before output reaches the worker. The live helper may
expose bounded sanitized events, but must not interpret them, answer questions,
approve plans, retry, or decide acceptance. The LLM verifier reads that stream
and the coordinator pane. The stream must be keyed by a non-forgeable host
worker identity, not by model-supplied text or a public durable identifier.

## Required acceptance gates

The fix is not eligible for another live attempt until all of these are green:

1. Registry, contracts, renderer, bundled skills, profiles, prompts, and
   documentation have one semantic catalogue and contain no retired public
   operations or MCP argument recipes outside advertised schemas/descriptions.
2. A first-call source and exact-candidate stdio test proves that an opened
   clarification hold is the only source of a visible clarification state, and
   that an answer creates exactly one decision and one delivery record.
3. Candidate tests cover coordinator-originated and worker-originated holds,
   duplicate open, record replay/conflict, stale/superseded hold, process
   restart, cross-task isolation, and unavailable same-worker continuation.
4. Host-adapter tests prove delivery only to the exact live worker capability;
   unavailable/ambiguous continuation produces no duplicate worker and uses the
   existing parent-linked recovery route only after coordinator choice.
5. Event-capture tests prove that a failed first worker publication and an
   unexplained replay remain visible to the LLM verifier while no private/raw
   prompt content is exposed.
6. The focused isolated live run observes exactly one durable clarification
   open, exactly one durable record after the operator answer, an explicit
   same-worker delivery or justified replacement, and one clean first worker
   publication event. Any tool/schema error or unexplained replay fails.

The broader planner → approval → implementation → independent verification →
documentation-impact → closure scenario remains required after the focused
gate. No existing orchestration capability is removed: dynamic DAG selection,
parallel non-overlapping work, advisory governance, parent-linked rework,
same-task steering, context recovery, worker-owned publications, historical
evidence, and host-visible live verification all remain preserved. The new
aggregate supplies the missing durable bridge between them.
