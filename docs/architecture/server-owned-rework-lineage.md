# Server-owned rework lineage

Development-only architecture record. This file is not a runtime contract and
must not be referenced by skills, prompts, profiles, or installed-plugin
documentation.

## Live finding

Final9 reached independent verification after a clean clarification, plan,
approval, implementation, in-flight steering, follow-on implementation, and
verification sequence. The verifier found a concrete accessibility defect.
The coordinator then supplied an older implementation assignment as the
rework parent while also declaring the verifier report and the latest
follow-on implementation report. `open_assignment` rejected the call with
`outcome_assignment_conflict` because ownership had already transferred to the
follow-on assignment.

The model's explicit parent was stale, but the server already possessed enough
immutable evidence to avoid asking for it: each declared report names its
publishing assignment, and current outcome ownership identifies which of those
assignments is the live predecessor. Requiring the model to restate that
identity duplicated server state and created another first-call failure mode.

## Selected design

`parent_assignment_ref` is removed from the public `open_assignment` input.
Assignment lineage remains output evidence in `relations`.

For an owner-profile assignment, the server:

1. resolves every declared finalized input report;
2. finds report authors that still own at least one current outcome item;
3. derives the unique current owner as the predecessor;
4. atomically reconciles its dispatch lease;
5. transfers its active ownership to the new assignment; and
6. returns the derived predecessor relation.

Review and documentation profiles never take ownership merely because they
consume an owner report. Plan revision lineage continues to be derived from the
immutable plan and review-decision relation. If owner evidence names multiple
incompatible current owners, the server rejects the request before mutation as
ambiguous; the model is not asked to choose or reconstruct one.

```text
declared immutable reports
        -> publishing assignments
        -> filter current active owners
        -> exactly one owner: derive predecessor and transfer
        -> zero owners: open independent owner scope from current contract
        -> multiple owners: stop before mutation with exact ambiguity
```

## Required evidence

- The advertised schema contains no caller-authored parent field.
- Initial implementation from plan evidence does not inherit planner ownership.
- A follow-on owner derives the first implementation as predecessor.
- A verifier consumes the follow-on result without taking ownership.
- Rework that declares verifier plus current-owner reports derives the current
  owner, not a stale ancestor, and opens on its first call.
- The live sequence completes rework, fresh verification, documentation impact,
  and closure without `outcome_assignment_conflict` or mutation replay.
