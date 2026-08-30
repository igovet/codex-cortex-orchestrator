# Phase C acceptance contract

Phase C is the parity and registry gate for the 1.12.1 architecture recovery.
It proves that every existing orchestration capability has an explicit owner
before a live run is allowed to claim success.

## Capability identity and preservation

The inventory in [orchestration-feature-parity.md](orchestration-feature-parity.md)
is the preservation source. Each matrix row has a deterministic audit ID
`cap-<lowercase-slug>`, derived from its exact label. The runtime registry has
the shorter immutable `feature_ids` vocabulary (for example,
`typed-evidence` and `candidate-provenance`) so multiple rows can share one
cross-cutting runtime capability. The acceptance test checks both layers and
requires the registry vocabulary to retain every required feature ID.
The acceptance test rejects missing labels, missing target owners, empty
acceptance tests, and any preservation status other than `Must preserve`.
Capabilities may move between owners during migration, but may not disappear
or be silently marked dropped.

## Registry acceptance

The semantic registry is the only source from which the MCP catalogue,
handler binding, contracts, typed capability edges, validator expectations,
and first-call conformance checks may be derived. The gate requires every
operation to declare capability IDs and an owner, every ID to exist in the
parity inventory, and every preserved capability to be covered by an
operation, policy owner, or explicitly documented non-operation owner.

Every producer/consumer edge must name both fields and pass the producer value
unchanged. A missing registry, edge, operation coverage, or source/catalogue
agreement is a failure, not a skip.

Phase C additionally requires receipt metadata to be unambiguous: every
command declares a logical-slot category and every query declares none. The
Domain Kernel owns canonical request normalization, digesting, preflight, and
receipt wiring; a vertical slice owns the slot derivation policy. Queries do
not pass through command receipts unless their domain contract explicitly
models a separate consumption mutation.

The coordinator remains the owner of model decisions: DAG adaptation, worker
selection, model/effort selection, user questions, approvals, governance
judgment, recovery/rework choice, evidence sufficiency, and final synthesis.
Registry and kernel acceptance must not turn those behaviors into autonomous
backend workflow.

## Required semantic surface

The single MCP server continues to expose:

`open_task`, `read_task`, `open_clarification`, `record_clarification`,
`open_plan_review`, `record_plan_review`, `open_steering`, `record_steering`,
`open_assignment`, `consume_assignment_evidence`, `publish_plan`,
`publish_result`, `publish_documentation`, `assess_governance`, and
`close_task`.

Retired physical report/chunk operations remain storage details; their
historical evidence and forward-only migrations remain covered by the parity
matrix. Removing a public behavior, worker lifecycle rule, governance route,
recovery path, security boundary, or live-dev observation requirement is a
parity failure even if the registry-derived catalogue remains available.

## Gate ordering

```text
source/candidate parity -> black-box stdio -> Phase C parity
-> vertical-slice durability -> LLM-driven live-dev E2E
```

These are zero-skip checks. Missing declarations require an architectural
change rather than a prompt hint or an unverified result.

Test process mode is explicit. Tests that execute the checkout directly must
set `CORTEX_SOURCE_MODE=1`, must remove any ambient `PYTHONPATH`, and must
assert `serverInfo.parityVerified=false`; this is source-mode evidence only.
Black-box candidate tests execute the staged candidate without
`CORTEX_SOURCE_MODE`, remove ambient `PYTHONPATH`, and must assert the stamped
identity and `serverInfo.parityVerified=true`. No test may treat source-mode
execution as candidate qualification.
