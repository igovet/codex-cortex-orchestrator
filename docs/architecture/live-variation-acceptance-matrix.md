# Live-variation acceptance matrix

Development-only design artifact. This matrix specifies observable behavior and
lineage invariants; it intentionally contains no tool argument names, request
shapes, payload examples, or model instructions. It must not be referenced by
the installed plugin contract.

## Global invariants

1. One live session has one isolated candidate identity, one registered runtime
   generation, and one immutable task anchor.
2. Every durable mutation has one canonical receipt. A repeated mutation is
   acceptable only when the transport result was genuinely ambiguous and the
   server returns an exact replay; an unexplained second success is a failure.
3. Native worker identity is bound to one server-issued assignment and one
   parent dispatch correlation. A worker cannot publish outside that scope.
4. SQLite rows, immutable evidence files, content digests, event chronology,
   read receipts, and downstream consumption must agree on the same lineage.
5. Observation failure never changes canonical mutation outcome, but any
   missing, stale, malformed, or contradictory evidence fails acceptance.
6. A replacement or rework assignment is allowed only after explicit semantic
   correction, a verified stale/conflict state, or an ambiguous transport that
   has been reconciled read-only. It is never a default retry.

## Scenario matrix

| Variation | Required sequence | Acceptance invariants | Failure conditions |
|---|---|---|---|
| Clean baseline | Isolated launch → candidate registration → task execution → worker publication → verification → closure | Candidate/build/catalogue identity is consistent; one task; one clean first worker publication; all evidence is readable | Any hidden tool error, replay, missing receipt, stale generation, or unverified candidate |
| Published-plan revision | Initial plan publication → explicit revision decision → new plan publication | Original plan remains immutable; revision has a new digest and chronology; approval targets only the selected revision | Old plan overwritten, revision without explicit decision, approval bound to stale digest, duplicate normal publication |
| Stop/resume same thread/task | Stop after durable checkpoint → resume same thread and task | Same task anchor and lineage resume; no new task or assignment solely due to pause; pending work remains reconciliable | New task/assignment without ambiguity, lost continuation, altered candidate identity, or replayed mutation |
| Mid-work user steering | Active worker → user steering event → effective contract update → continued work | Steering is recorded once; affected scope revision is explicit; downstream workers consume the new current scope; prior evidence remains immutable | Silent scope mutation, worker using retired scope, unrecorded steering, or replacement used instead of reconciliation |
| Clarification question/answer | Server-issued hold → one rendered question → user answer → recorded decision → continuation | One matching hold; answer is recorded once; continuation preserves task/assignment lineage; no work proceeds on unresolved hold | Question reconstructed from prose, answer recorded against another hold, duplicate binding, or work proceeding before resolution |
| Plan approve/revise/cancel | Ready plan → one user choice → approval, revision, or cancellation path | Approval binds exact ready plan; revision creates a distinct immutable revision; cancellation closes the pending review without publication | Approval of non-ready/stale plan, choice applied twice, cancellation mutating evidence, or revision overwriting prior plan |
| Ambiguous transport reconciliation | Mutation request → ambiguous transport result → read-only reconciliation | Reconciliation finds exact success, exact pending state, or exact conflict/stale state; identical request is retried only when server state proves it is pending | New identity created by default, changed semantic input, blind retry, duplicate success, or unresolved ambiguity treated as completion |
| Report-context lineage | Worker consumes declared predecessors → publishes evidence → coordinator reads immutable report → downstream worker consumes it | SQLite report row, immutable file, digest, event sequence, read receipt, and downstream input all identify one immutable report lineage | Missing predecessor consumption, digest mismatch, file/row disagreement, unreceipted body read, cross-task consumption, or altered downstream evidence |

## Lineage proof requirements

For each worker event, acceptance requires a closed chain:

```text
runtime generation
  → assignment/worker identity
  → event sequence
  → SQLite canonical row
  → immutable evidence file and digest
  → read receipt
  → downstream consumption receipt
```

Every arrow must be verifiable from bounded structured evidence. A final report
reference without the preceding chain is insufficient.

## Operational outcome classes

- **Pass:** all required transitions and lineage checks succeed with no hidden
  errors or unexplained repeats.
- **Fail:** any invariant is contradicted, any required evidence is absent, or
  a mutation is repeated without an explicitly ambiguous prior transport result.
- **Unverified:** the session or candidate provenance cannot be proven; no
  semantic acceptance decision may be made.
- **Blocked:** a closed stale/conflict/security condition prevents safe
  continuation; preserve evidence and stop the affected branch.
