# Production incident protocol

Use for production failure/restoration. Apply the actual system's predicates;
trading and Redis examples do not become requirements for unrelated work.

## Continuous coordination and one full brief

The coordinator owns the complete causal model and user outcome across specialist
handoffs. For an unknown complex incident, assign focused diagnosis, implementation
based on its evidence, and independent verification. For a understood nontrivial
change, implementation with self-tests plus a separate verifier/reviewer suffices;
do not add an investigator without an unanswered question. A low-risk trivial
change may use one executor when independent acceptance is not required.
Production, security, migrations and substantial behavior changes need explicit
independent verification. Preserve each specialist's assignment ownership through
timeouts, but do not confuse continuity with one agent doing every phase. Choose
each model by role and complexity under worker routing, never by predecessor model.
Do not impose a numerical agent cap; each specialist needs a concrete contribution.

Before the first patch, record inside the current pipeline:

- User outcome, safety state, scope and rollback plan.
- Git revision, deployed image and loaded module identities separately; actual
  runtime configuration, production schemas and data versions.
- Full event-to-outcome path, startup order, transition owners and every relevant
  admission, lifecycle, lease, budget, ledger, receipt and rollback predicate.
- Existing safe-to-working transition and final predicate enabling the user action.
- Read-only production snapshot and nearest local reproduction.
- Observed facts, hypotheses, discriminating check, unchecked downstream predicates
  and why the proposed patch addresses a defect class rather than one value.

Use hypothesis until tested, isolated_root_cause for a reproduced local failure,
and end_to_end_root_cause only after checking the full path and next transition.
Never call a local blocker the only cause while downstream conditions remain open.

## Rehearse before deployment

Use a disposable production-shaped rehearsal with sanitized schema, key types,
required receipt fields/versions, lifecycle ordering and activation/rollback
contracts. Use a fake provider with counted external calls. Exercise success,
replay, conflict, tamper, wrong key types, partial/legacy schema and rollback.
Prove zero provider calls for denied or erroneous new actions. Where applicable,
reductions/closures remain available while new openings are denied; check Redis
types before mutation. No private payloads or raw production logs enter fixtures.

Production must not be the first dynamic integration test because time or a local
dependency ran out. If rehearsal is impossible, record the risk and limit production
actions to read-only preflight. Missing rehearsal cannot be labeled PASS.

## Replan on new evidence

After any previously unknown live/preflight failure class, record before another
patch: the unknown contract, why snapshot/rehearsal missed it, adjacent predicates,
the new class-covering check and the disproved part of the causal model. Summarize
what was proved, disproved, changed and why a next attempt differs.

After two distinct unknown blockers in one rollout series, end the deploy loop and
re-baseline: reread the full runtime path, refresh the snapshot, check every remaining
predicate, prepare one cohesive change and rerun the complete rehearsal. This is a
conditional model response, never a server stage or runtime prohibition.

Per artifact/check identity: one rehearsal, one risk-appropriate independent review,
one deploy, one production preflight and one activation attempt. Repeat only after
an artifact change or concrete new external evidence, naming the delta. Successful
CI is not deployed runtime; deployed runtime is not working production; DENY is not
restored trading. Accept only the requested outcome at the exact deployed revision.

Use the optional incident_decision block once per material decision. Reference the
existing delivery/acceptance/causal-delta/receipt fields instead of duplicating them.
