# Public publication coverage root-cause analysis

Development-only analysis. This file is not a runtime contract and must not be
referenced by skills, prompts, profiles, or installed-plugin documentation.

## Finding

The current publication boundary contains two contradictory authorities.

The public publication description says the server derives coverage from the
bound continuation scope and calls any caller annotation non-authoritative.
However, the store's terminal publication path still requires
`contract_coverage`, requires one claim per assigned item, resolves every
caller-supplied `item_ref`, rejects extra/duplicate/missing references, and may
reject a substantively complete report before storing it.

This creates a first-call reliability defect rather than a mere formatting
problem. The worker must reproduce a server-owned assignment set using opaque
item references, and one stale, omitted, duplicated, or cross-assignment
reference turns an otherwise complete report into `contract_coverage_*` or
`coverage_evidence_missing` failure. The continuation already carries the
assignment and effective-contract revision, so the server has the identity
needed to determine the expected set without asking the model to restate it.

Relevant evidence is in:

- `plugins/cortex/scripts/cortex_runtime/public_contracts.py`: publication
  descriptions state that coverage is derived from the bound assignment, while
  `contract_coverage` remains in the advertised evidence object;
- `plugins/cortex/scripts/cortex_runtime/v12_store.py`: terminal publication
  validation still requires and resolves caller `contract_coverage` claims;
- the same store path already derives active scope rows from the continuation
  assignment/revision and persists `report_contract_coverage` rows in the same
  transaction after that validation.

## Ranked designs

### 1. Server-owned coverage identity, worker-owned evidence (recommended)

Remove `contract_coverage` and all caller-supplied item references from the
public publication input. Keep the continuation as the sole scope authority.
The server derives the exact ordered item set from the immutable assignment
revision and persists coverage rows atomically.

The worker continues to provide substantive evidence through the existing
report fields: summary, outcome, stage checks, verification facts, risks,
deviations, unresolved work, and documentation impact. The server may mark an
assignment fully covered only when the required semantic evidence and
independent verification facts are present; it must mark missing or
unverified evidence as incomplete/rework rather than infer success.

Benefits:

- no model reconstruction of opaque item references;
- no extra/missing/duplicate coverage-reference failures;
- continuation and assignment revision remain the only identity authority;
- the already-existing server-derived persistence path becomes the only path;
- report completeness and independent verification are preserved.

Necessary source correction:

- delete the terminal precondition that rejects absent `contract_coverage`;
- delete caller-claim resolution and duplicate/extra/missing reference checks;
- derive rows from the exact continuation assignment scope in one transaction;
- retain semantic evidence gates and return precise diagnostics when report
  content lacks required verification, outcome, or documentation evidence.

This is the smallest architectural correction because the store already has
the required assignment-scope derivation and persistence code.

### 2. Server-owned identity with ordered worker annotations

Keep a bounded worker-owned array of per-scope annotations but remove
`item_ref`. The server maps array position to the ordered continuation scope:

```text
coverage_annotations: [
  {status, verification},
  {status, verification},
  ...
]
```

The backend requires exactly the server-derived number of entries and maps
position to item identity. This removes opaque-reference errors while retaining
per-item verification detail.

Risks:

- the model can omit, reorder, or duplicate semantic entries even without an
  explicit reference;
- a changed scope must invalidate the annotation array rather than silently
  remap it;
- the array remains a second caller-owned representation of server state.

Use this only if per-item verification is a hard product requirement that
cannot be derived from report/stage evidence. It is safer than caller item
references but less reliable than fully server-owned coverage.

### 3. Keep caller `contract_coverage`, improve descriptions and retries

Retain the current schema and add stronger wording/examples or a retry path.

This does not solve the root cause. The model still has to copy server-owned
opaque references into a second structure, and a successful retry would only
mask the same architectural mismatch. It also conflicts with the current
description that coverage is non-authoritative.

### 4. Dynamic per-continuation publication schemas

Issue a schema customized to the assignment after evidence consumption.

Static MCP `tools/list` admission in Codex does not provide a reliable
per-binding schema refresh. This would introduce host-dependent behavior and
does not remove the need for server-side scope authority. It is not suitable
for the one-MCP constraint.

## Invalid-state matrix

| State | Current caller-reference design | Recommended server-owned design |
|---|---|---|
| Complete report, one omitted item ref | Rejects as incomplete | Server derives the full expected set |
| Complete report, stale item ref | Rejects as extra/unassigned | No caller item identity to become stale |
| Duplicate item ref | Rejects as duplicate/incomplete | Impossible at the input boundary |
| Correct evidence, wrong ordering | May persist wrong association or reject | Scope order is server-owned |
| Missing verification evidence | Reject/rework | Still reject/rework; no false completeness |
| Partial/blocked outcome | Derived status remains non-complete | Same non-complete status, server-derived |
| Changed contract revision | Caller refs may target the wrong snapshot | Continuation revision selects exact scope |
| Replay | Existing report replay semantics remain | Same replay semantics and immutable derived rows |

## Recommendation and acceptance criteria

Adopt design 1. The continuation's server-owned assignment scope should
eliminate coverage references from publication input. The server must remain
strict about substantive evidence, independent verification, acceptance
criteria, risks, deviations, unresolved work, and documentation impact. Removing
caller identity does not mean accepting an empty or unsupported report.

Acceptance requires:

1. A complete plan/result/documentation publication succeeds without a
   `contract_coverage` field.
2. The same publication persists one derived coverage row per active item in
   the immutable assignment scope.
3. A missing verification fact, incomplete outcome, or missing documentation
   impact still produces deterministic rework/incomplete status.
4. Scope changes select a new continuation/revision; old continuation scope is
   never remapped to the current task contract.
5. Real stdio first calls prove that workers can publish from the advertised
   schema plus consumed assignment evidence, without copying item references.
6. Replay, conflict, stale continuation, concurrent publication, independent
   verification, and cross-assignment isolation remain covered.

The key invariant is: the worker supplies evidence about its assigned work;
the server supplies the identity of what was assigned. A publication cannot
become incomplete merely because the model failed to restate server-owned
identity, but it also cannot become complete without truthful substantive and
independent evidence.

## Initial-owner scope invariant

A task does not require a synthetic planner stage before bounded direct
execution. When an owner-policy profile opens the first assignment without
predecessor reports, the server assigns the complete current effective
catalogue to that owner. Review-policy profiles receive the same catalogue as
contributing/evidence scope without taking ownership. Parent-linked owners
continue to inherit only the predecessor's active scope plus newly introduced,
currently unowned items from later revisions.

This distinction prevents a completed first-worker publication from producing
`no_owned_delegation` for every contract item while preserving strict review
separation and rework lineage.
