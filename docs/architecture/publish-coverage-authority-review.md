# Publication coverage authority review

Development-only architectural review of the run2v `publish_plan` failure
mode. This note is not a runtime contract and must not be referenced by
packaged skills, prompts, schemas, or production code.

## Finding

Requiring an LLM to copy the entire server-issued contract catalogue into a
terminal publication is a fragile boundary. The server already owns the
authoritative item set (task, effective revision, assignment scope, retired
state, and ownership). Requiring the model to reproduce every opaque item
reference creates a second serialization of server state and makes harmless
copy omissions, substitutions, and stale references look like worker
failures. It also encourages prompt-level parameter recipes, which are
explicitly prohibited.

The current strict check is nevertheless protecting a real invariant: a
report must say which items were assessed and with what status/evidence. Simply
removing `contract_coverage` would lose that invariant and allow a result to
claim completion without per-item support. Therefore “make coverage optional”
is not a safe architectural fix.

## Recommended boundary

Split authoritative coverage identity from worker-authored assessment:

1. In the same continuation/publication transaction, the backend derives an
   immutable `coverage_manifest` from the assignment and current effective
   contract. For a planner it is the full planning catalogue; for every other
   worker it is the current non-retired assigned scope. The manifest records
   item IDs, compact refs, category, ordinal, revision, and assignment role.
2. The worker submits semantic evidence and statuses. The worker may annotate
   a manifest item, but it does not submit the authoritative item set. The
   server resolves any supplied item references against the manifest and
   rejects unknown, duplicate, retired, cross-task, or stale entries.
3. The backend attaches the manifest to the immutable report and records the
   worker's per-item assertions in `report_contract_coverage`. Missing
   assertions are represented explicitly as `unverified` (or a distinct
   `not_assessed` state), never silently treated as complete. Completion gates
   then require every manifest item to have an acceptable assertion according
   to the publication family.
4. A planner's plan can therefore be accepted without asking the model to
   reproduce the full catalogue, while its stage/evidence text remains model
   authored. A result/review worker still has to provide item-level assessment
   where the governance policy requires it; the server supplies the required
   universe and performs the completeness check.

This preserves the important distinction between server-owned scope and
worker-owned evidence. It removes the “copy every opaque handle exactly”
failure from the primary path without weakening stale/cross-task protection.

## Alternatives rejected

- Inferring item coverage from free-form mission prose, report headings, or
  stage text is unsafe and non-deterministic.
- Mapping an array by ordinal is unsafe after contract edits, retirement, or
  parallel assignments.
- Accepting a worker-provided coverage list as authoritative duplicates the
  current failure boundary and permits omission.
- Auto-marking every server-derived item `complete` would turn absence of
  evidence into false completion.

## Required migration and acceptance tests

- A first publication with semantic evidence but no duplicated full catalogue
  receives a server-attached manifest and an explicit incomplete/unverified
  result rather than a schema-copy validation error.
- A publication with all required per-item assertions succeeds and stores the
  exact server-derived manifest once.
- Unknown, duplicate, cross-task, retired, and stale-revision annotations fail
  closed in the same atomic transaction as report uniqueness and continuation
  validation.
- Replay returns byte-equivalent report and manifest; a changed assertion for
  the same assignment/kind is a conflict, not a second report.
- Planner, implementation, QA, review, documentation, rework, and parallel
  non-overlapping assignments each derive the correct manifest without prose
  routing.

Until this split is implemented, the existing strict `contract_coverage`
requirement should remain: it is brittle, but it is safer than silently
accepting an unscoped terminal report.
