# Phase D adversarial review: Decision cutover

Status: source review clear through D-ADV-014; exact-candidate and live-dev
gates remain open under D-ADV-013.

This is a read-only adversarial review of the six-operation Decision boundary,
its `DecisionAggregate`, MCP composition, and the qualification tests. It does
not claim candidate or live evidence. The only changes made for this review
are this sanitized document and its architecture-index links. No private logs,
credentials, opaque runtime values, or user data are recorded here.

## Review scope and evidence boundary

Reviewed surfaces:

- `plugins/cortex/scripts/cortex_runtime/semantic_registry.py`
- `plugins/cortex/scripts/cortex_runtime/public_contracts.py`
- `plugins/cortex/scripts/cortex_runtime/domain_api.py`
- `plugins/cortex/scripts/cortex_runtime/domain_kernel.py`
- `plugins/cortex/scripts/cortex_runtime/v12_store.py`
- `plugins/cortex/scripts/cortex_runtime/mcp_api.py`
- `plugins/cortex/scripts/cortex.py`
- `tests/test_phase_d_decision_qualification.py`
- `tests/test_decision_aggregate.py`
- `tests/test_public_mcp_first_call_conformance.py`
- `tests/test_semantic_registry.py`
- `docs/architecture/decision-capability-parity-matrix.md`

The code knowledge graph was ready with no recorded parse gaps for the cited
source and test paths. Direct source inspection was still used for all
negative claims and for the MCP result projection. This review did not run
live-dev, refresh a cache, modify the installed profile, or change production
or test code.

## Current result-schema contract

The reviewed runtime now separates discovery from validation. Every semantic
tool advertises a compact public `outputSchema` containing only the essential
handles, lifecycle states, replay/continuation information, and next-action
data needed to navigate results. The complete closed result schema is retained
as a private runtime contract and remains authoritative for successful-result
validation. This keeps the complete fifteen-tool catalogue within the bounded
discovery response without weakening validation. A deferred mutation lookup is
safe only when it retrieves the specific intact live declaration it needs; a
missing or truncated declaration must fail closed rather than guessing a
mutation contract.

## Findings

Severity uses `P0` for a blocker that makes the public path unusable or loses a
required invariant, and `P1` for a serious contract or parity defect that must
be fixed before candidate qualification.

| ID | Severity | Finding | Evidence | Required root fix |
| --- | --- | --- | --- | --- |
| D-ADV-001 | P0 | The public open operations do not deliver a consumable binding handle. | `domain_api.open_clarification`, `open_plan_review`, and `open_steering` return the raw `DecisionAggregate.open_*` result, whose binding is nested under `binding.clarification_binding`. `mcp_api._handles` only reads a top-level `binding_ref` and never reads that nested binding. Therefore the successful MCP result gets an empty `handles` object and the next record call cannot copy a server-issued binding. `public_contracts.py` describes a top-level `binding_ref`, so the implementation and advertised result contract disagree. | Define one family-result projection at the semantic adapter boundary. It must expose exactly one scalar family binding in `structuredContent.handles`, preserve the full non-callable context separately, and be covered by a real stdio first-call test for each family. Do not make the model reconstruct a nested canonical ID. |
| D-ADV-002 | P0 | Compact public entity references are passed to canonical-ID-only storage methods. | `public_contracts.open_plan_review` advertises `plan_ref` as a compact `r_...` locator. `domain_api.open_plan_review` passes it unchanged to `DecisionAggregate`, and `issue_clarification_binding` stores it through `_record_identifier`; `_report` later requires a canonical sharded report ID. The same mismatch exists for optional compact `subject_ref` and `assignment_ref` in `open_clarification` and `open_steering`. | Resolve every typed compact locator exactly once at the adapter/kernel boundary with `V12Store.for_record_ref`/the corresponding resolver, verify same-task ownership, and pass only the resolved canonical ID into the transaction. Store only canonical IDs; return only typed compact handles. Add cross-family and cross-project black-box cases. |
| D-ADV-003 | P0 | The six-operation tests still exercise the retired overloaded public pair and cannot qualify the cutover. | `tests/test_phase_d_decision_qualification.py` calls `open_decision` and `record_decision`; `tests/test_public_mcp_first_call_conformance.py` still asserts the old producer/consumer edge and sends the old nested binding shape; `tests/test_marketplace_release_gate.py` and `tests/test_v12_compatibility.py` also import/call the retired pair. This is inconsistent with `tests/test_semantic_registry.py`, which expects 15 operations and forbids those names. | Replace qualification and release-gate scenarios with exact-candidate stdio calls to all six family operations. Remove old public aliases from test fixtures and composition assertions. Preserve historical rows only as read evidence, never as a callable compatibility protocol. |
| D-ADV-004 | P1 | Steering delta is not part of the command-receipt request digest. | `DecisionAggregate.record` builds its receipt request from task, binding, response, user language, and optional decision-type override, but omits `steering_delta`. `record_steering` passes the delta only to the mutation callback. A retry using the same binding and response with a changed delta therefore matches the old receipt and replays instead of returning a conflict. | Include the canonical steering intent (and every other family-specific semantic input) in the receipt request before `run_command_receipt`. Verify same-input replay and changed-delta conflict with no second revision or decision. |
| D-ADV-005 | P1 | Public steering does not expose the required supersession relation. | `decision-api-schema.md` and the parity matrix require steering supersession, and the store accepts `supersedes_decision_id`. However `domain_api.record_steering` and its public schema accept only task, binding, response, language, and delta; they never pass a superseded decision reference. The family operation cannot preserve this existing orchestration capability. | Make supersession a typed, closed steering relation resolved from a server-owned eligible decision set, or return that relation through a server-owned steering context. Include it in the receipt digest and atomically persist it with the effective-contract revision. |
| D-ADV-006 | P1 | Binding issuance and command identity are not computed in one transaction. | `DecisionAggregate.open` calls `_revision` through a read transaction before computing the logical receipt slot. `issue_clarification_binding` then reads the current revision inside the receipt transaction. A concurrent steering can advance the revision between these reads, leaving the receipt slot derived from one revision while the binding is issued for another; a subsequent identical open can use a second slot. | Resolve the current effective-contract snapshot, derive logical identity, issue/replay the binding, and insert/replay the command receipt under one `BEGIN IMMEDIATE`. A concurrent revision must yield one deterministic stale/conflict result, never split receipt identity. |
| D-ADV-007 | P1 | The steering nested input schema is open-ended, contradicting the closed-schema architecture. | `public_contracts.record_steering` declares `steering_delta` as `{"type": "object", "additionalProperties": True}` while the architecture requires concrete closed schemas and the store accepts only `retire_item_refs` and `add` with closed addition objects. The advertised contract invites calls that can only fail after dispatch. | Generate the steering delta schema from the same registry contract as the handler, set `additionalProperties: false` at every object level, and keep backend validation as the final invariant check. |
| D-ADV-008 | P1 | Runtime instructions and guard messages still claim an eleven-tool catalogue. | `plugins/cortex/scripts/cortex.py::SERVER_INSTRUCTIONS` says participants receive “the same eleven semantic tools”; `build_v12_public_tools` raises an error mentioning the “canonical eleven-tool catalogue”; `public_contracts.build_public_contracts` has the same stale eleven-tool message. The registry currently contains 15 operations. | Derive the count and wording from `OPERATION_NAMES` or use count-neutral instructions. Add a first-call test that compares initialize instructions, tools/list, registry order, and handler order. |
| D-ADV-009 | P1 | Legacy callable aliases remain exported from the semantic adapter. | `domain_api.__all__` still exports `issue_clarification`, `open_decision`, and `record_decision`; those functions retain overloaded or historical argument shapes. The MCP composition map no longer exposes them, but direct imports and stale qualification paths keep the second protocol alive. | Remove legacy names from the semantic public export and test surface. Keep only non-callable historical read/migration logic internally. Any migration adapter must not accept the old MCP request shape. |
| D-ADV-010 | P1 | Family output schemas were not actually advertised on the MCP wire. | Historical finding: the prior implementation substituted a generic wire schema and kept family validation internal. | Resolved by advertising a compact registry-generated public projection for each operation while retaining the complete closed family schema privately for runtime validation; the bounded catalogue and strict separation are covered by `tests/test_public_schema_separation.py`. |

## Invariant coverage matrix

The following table records the review result against the required Decision
invariants. `Blocked` means the source currently contains a defect or the
required proof is absent; it is not a candidate or live pass.

| Invariant | Source review result | Candidate/live evidence required next |
| --- | --- | --- |
| One server-owned scalar binding is emitted and consumed by the matching family record operation | **Blocked** by D-ADV-001 and D-ADV-002 | Exact-candidate `tools/list` plus one first-call open/record pair for clarification, plan review, and steering; compare the copied scalar byte-for-byte. |
| Same logical open returns the same binding across concurrency and restart | **Partially implemented, not proven**; D-ADV-006 can split receipt identity under a revision race | Two concurrent opens, process restart, and a concurrent steering race in one exact candidate package. |
| Record transaction includes binding consumption, decision, timeline, effective revision, and command receipt | **Partially implemented** in `run_command_receipt` and store writes; family cutover cannot be accepted while public handles are unusable | Failure injection/rollback plus committed-state inspection through read-only projections, then restart reconciliation. |
| Exact replay and changed-input conflict | **Blocked** for steering delta by D-ADV-004; old tests use the retired pair | Same family binding with exact replay, changed response, changed steering intent, and no second mutation. |
| Stale, wrong-family, wrong-type, and cross-project references fail closed | **Partially implemented** in store checks; compact-to-canonical adapter gap remains | Candidate calls using exact handles from another family/project and stale contract revisions, with sanitized semantic error envelopes. |
| Immutable plan/view relation for approve, request revision, and cancel | **Not qualified**; plan compact ref path is blocked by D-ADV-002 | Candidate obtains a server-issued plan/view relation and runs all three outcomes, asserting digest/view/sequence and no accidental approval. |
| Steering effective-contract revision and supersession | **Blocked** by D-ADV-004 and D-ADV-005 | Candidate records one delta and explicit supersession, asserts exactly one next revision, then exact replay and changed-delta conflict. |
| Backend does not author questions, schedule workers, interpret answers, or auto-approve | **No scheduler found in the aggregate path**; public cutover still requires qualification | Candidate side-effect assertions plus live LLM observation of question, answer, approval, and orchestration choices. |
| Safe errors and content-safety boundary | **Partially implemented**; transport sanitizes generic failures, but family-specific invalid calls are not qualified | Candidate malformed/stale/cross-project calls; no traceback, raw private data, or incomplete writes. |
| Full orchestration feature parity | **Not accepted**; the parity matrix remains the authority and D-ADV-003 shows qualification drift | Re-run every affected parity row, then proceed to assignment/evidence and publication gates. |

## Required order of remediation

The following order is architectural, not a suggestion to patch individual
symptoms:

1. Make the semantic adapter the single typed boundary: resolve compact
   locators, shape family results, and remove legacy callable exports.
2. Make the Decision aggregate transaction own the complete identity snapshot,
   family command name, receipt digest, binding, decision, timeline, effective
   revision, approval relation, and steering supersession as one unit.
3. Generate closed input and typed output contracts from the registry and bind
   the exact same registry order to handlers, wire tools, instructions, and
   qualification cases.
4. Rewrite the exact-candidate tests around the six operations and run the
   complete invariant matrix, including restart, concurrency, rollback, and
   all plan outcomes.
5. Only after the candidate matrix is green, run the required real LLM-driven
   live-dev decision slice. A transport/helper may expose observations but may
   not answer the question, approve the plan, or classify acceptance.

Until D-ADV-001 through D-ADV-003 are resolved, the Decision vertical slice
must remain blocked. A source-level pass or a final decision reference cannot
override an unusable first-call binding path or qualification tests that still
exercise the retired protocol.

## Re-review — 2026-08-29

This section is an independent re-review after the Phase D remediation. It is
based on the current production source and test files, not on a remediation
report. No production or test files were changed during the re-review. The
focused test command was attempted with `python3 -m pytest` for the public MCP
and Phase D suites. The five public MCP tests passed; the ten Phase D cases did
not start because the candidate builder rejected pre-existing/generated
`plugins/cortex/scripts/__pycache__` runtime state. No candidate MCP process or
live-dev session was launched. The generated state must be removed by the
coordinator before candidate qualification can begin.

### D-ADV-001 through D-ADV-010 disposition

| Finding | Re-review result | Current evidence |
| --- | --- | --- |
| D-ADV-001 — binding not exposed | **Resolved in source** | `domain_api._family_result` now projects a top-level scalar `binding_ref`; `test_public_mcp_first_call_conformance.py` checks the family producer/consumer fields. Candidate execution remains unverified because the builder stopped before launch. |
| D-ADV-002 — compact refs passed as canonical IDs | **Resolved in source** | `domain_api` resolves subject, plan, assignment, and supersession locators through `V12Store.resolve_task_reference` before invoking the aggregate; the Phase D suite includes compact plan/subject/assignment calls. Candidate execution remains unverified. |
| D-ADV-003 — stale qualification protocol | **Resolved in source/test surface** | The Phase D, public-MCP, compatibility, and marketplace tests now use the six family operations; old names remain only in explicit retired-name assertions and private underscore-prefixed helpers. |
| D-ADV-004 — steering delta omitted from receipt | **Resolved in source/test surface** | `DecisionAggregate.record` includes steering delta and supersession in the receipt request; `tests/test_decision_aggregate.py` asserts changed delta produces `command_conflict`. Candidate execution remains unverified. |
| D-ADV-005 — steering supersession absent | **Resolved in source/schema** | `record_steering` accepts the closed optional supersession locator, resolves it within the task, and passes it to the aggregate; the Phase D scenario exercises an explicit supersession. |
| D-ADV-006 — revision read outside transaction | **Resolved in source** | `DecisionAggregate.open` uses `run_command_receipt_resolved`, which acquires the write transaction before resolving revision, logical slot, request digest, and mutation. Direct aggregate concurrency coverage exists; candidate coverage is blocked by builder preflight. |
| D-ADV-007 — open-ended steering delta | **Resolved in source/schema** | The nested steering delta and addition objects are closed and require at least one operation; public first-call checks inspect closed input roots. |
| D-ADV-008 — stale eleven-tool language | **Resolved in source** | Initialize instructions and composition guard derive the operation count from `OPERATION_NAMES`; no stale eleven-tool executable text remains in the reviewed composition/contracts. |
| D-ADV-009 — public legacy aliases | **Resolved in source** | `domain_api.__all__` exports only semantic family operations; historical helpers are underscore-prefixed and are not in the MCP handler map or registry. |
| D-ADV-010 — generic output schema on wire | **Resolved in source/test surface** | `serve_stdio` now copies each contract's compact public `outputSchema` into `tools/list`, while dispatch validates successful results against `runtimeOutputSchema`; `tests/test_public_schema_separation.py` proves the separation and strict rejection. Candidate execution remains unverified. |

### New findings from the re-review

| ID | Severity | Finding | Evidence | Required root fix |
| --- | --- | --- | --- | --- |
| D-ADV-011 | P1 | Receipt provenance records the wrong semantic command name for two families. | `DecisionAggregate.open` derives `open_steer` for the public `open_steering` operation. `DecisionAggregate.record` derives `record_clarification` when called through `record_steering`, and derives `record_approve`, `record_request_revision`, or `record_cancel` through `record_plan_review`. The registry and handler names are `open_steering`, `record_steering`, and `record_plan_review`. The logical slot and request digest are therefore attached to receipts whose `command_name` does not identify the public operation. | Pass the registry operation name explicitly through each family aggregate method, or map family methods to fixed command names. Persist exactly the public semantic operation name in every receipt and assert it through candidate qualification. |
| D-ADV-012 | P1 | Plan review does not bind the immutable approval-view relation at open time. | `open_plan_review` stores only the canonical plan subject, prompt, language, and effective revision in `clarification_bindings`. On record, `DecisionAggregate.record` selects the newest unconsumed `approval_handles` row for the plan (`ORDER BY created_sequence DESC`) instead of resolving a relation captured by the review binding. `request_revision` and `cancel` do not validate an approval-view relation at all. If a plan has more than one ready view/approval handle, the response can be recorded against a different view than the one presented; the current candidate test checks readiness before opening but does not assert the selected digest/view/source sequence in durable receipt evidence. | At `open_plan_review`, atomically resolve and persist the exact plan digest, view digest, source sequence, and approval relation (or a server-issued review handle containing that relation). At record, consume only that bound relation for all three outcomes; never select a mutable latest row. Extend candidate tests to create competing views and assert the exact relation and receipt. |
| D-ADV-013 | P1 | Candidate qualification is still blocked by package hygiene before any Decision call. | The attempted focused command passed five public-MCP tests but all ten Phase D fixture setups failed in `scripts/cortex_release_candidate.py::_plugin_payload_files` because `plugins/cortex/scripts/__pycache__` was present. The builder correctly fails closed, but no exact-candidate Decision behavior was exercised. | Remove only the generated runtime state, rerun the exact-candidate suite, and keep the candidate builder as a mandatory preflight. Do not bypass the builder or classify source-mode tests as candidate evidence. |

### Re-review gate status

The re-review above recorded the state before the following bounded source
remediation. It must not be read as current candidate or live evidence.

### Source remediation update — D-ADV-011 and D-ADV-012

| Finding | Current source disposition | Source evidence | Remaining acceptance gate |
| --- | --- | --- | --- |
| D-ADV-011 — receipt provenance | **Resolved in source** | `DecisionAggregate` uses one family-to-public-operation map for `open_clarification`, `record_clarification`, `open_plan_review`, `record_plan_review`, `open_steering`, and `record_steering`. `tests/test_decision_aggregate.py::test_receipts_use_public_semantic_family_names` checks the persisted names, including approve/request-revision/cancel. | Exact-candidate stdio must inspect receipt provenance for all six operations. |
| D-ADV-012 — immutable plan/view relation | **Resolved in source** | v17 extends `clarification_bindings` with plan digest, approval handle, view digest, and view source sequence. `open_plan_review` resolves/persists that relation under the receipt transaction; all record outcomes validate it without a current/latest lookup. `test_plan_review_binding_survives_newer_view_concurrency_and_restart` creates a newer competing view/handle, concurrently records approval, and verifies restart replay. | Exact-candidate stdio must exercise each outcome and inspect the persisted relation after a competing ready view. |

Phase D therefore remains **unaccepted**, not because D-ADV-011 or D-ADV-012
remain source defects, but because no exact-candidate or live-dev evidence is
claimed. D-ADV-013 is likewise not reclassified here: no candidate builder was
run as part of this source-only remediation. The next valid gates are:

1. Run the exact-candidate suite after its package-hygiene preflight, covering
   competing plan views, public receipt names, rollback, concurrency, and
   restart reconciliation.
2. Only after a clean candidate run, perform the required LLM-driven live-dev
   decision slice and inspect coordinator plus bounded native-worker events.

## Third re-review — 2026-08-29

This is the independent source-only pass after the D-ADV-011 and D-ADV-012
remediation. It inspected the current production source, migration v17,
maintenance health checks, and focused tests. No production or test files were
changed; no candidate, live-dev session, installed profile, or cache was
touched. The focused source suites passed:

```text
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q \
  tests/test_decision_aggregate.py \
  tests/test_v12_compatibility.py \
  tests/test_public_mcp_first_call_conformance.py
14 passed, 6 subtests passed
```

### D-ADV-011 and D-ADV-012 verification

| Finding | Third-review result | Current evidence |
| --- | --- | --- |
| D-ADV-011 — receipt command naming | **Resolved in source and focused tests** | `DecisionAggregate._FAMILY_OPERATIONS` is the single family-to-public-operation map; open and record pass `self._family_operation(family, index)` to the receipt store. `tests/test_decision_aggregate.py::test_receipts_use_public_semantic_family_names` checks `open_plan_review`, `record_plan_review`, `open_steering`, and `record_steering`, and rejects outcome/internal aliases. |
| D-ADV-012 — immutable plan/view relation | **Resolved in source and focused tests** | `issue_clarification_binding` calls `_ready_plan_review_relation` inside the open receipt transaction and stores plan digest, approval handle, view digest, and source sequence on the binding. Record reads only those persisted columns; it does not select a newest approval handle or current projection. `tests/test_decision_aggregate.py::test_plan_review_binding_survives_newer_view_concurrency_and_restart` verifies relation preservation, concurrent exact replay, and restart replay. |

### D-ADV-001 through D-ADV-010 regression check

No new P0/P1 regression was found in this pass. The current fifteen-operation
registry, handler map, compact public result projection, private closed runtime
schemas, compact-reference resolution, wire `outputSchema`, and legacy-name exclusions remain aligned. The
focused source suites provide positive evidence for family handles,
schema/wire contract, command naming, and plan relation. Candidate and live
evidence remain separate required gates.

### New finding from the third re-review

| ID | Severity | Finding | Evidence | Required root fix |
| --- | --- | --- | --- | --- |
| D-ADV-014 | P1 | Maintenance health can report a v17 database as schema-healthy without checking the v17 plan-relation columns. | `plugins/cortex/scripts/cortex_runtime/v12_maintenance.py` includes migration `(17, "v17-plan-review-bound-relations")` and the `clarification_bindings` table in `_REQUIRED_TABLES`, but `_REQUIRED_COLUMNS["clarification_bindings"]` stops at the v15 columns and omits `plan_content_digest`, `plan_approval_handle`, `plan_view_content_digest`, and `plan_view_source_sequence`. A database can therefore pass the migration-list/table checks while maintenance health says `schema=true`, even though v17 relation columns are absent; ordinary `V12Store` later fails closed or migrates them. | Add all v17 relation columns to the maintenance required-column contract and add a maintenance health regression using a schema missing one of those columns. Keep migration forward-only and make health and runtime validation agree. |

### Third-review gate status

D-ADV-011 and D-ADV-012 are source-cleared and focused-test-backed. Phase D
remains **blocked** by D-ADV-014 until maintenance health validates the full v17
schema, and by D-ADV-013 until the exact candidate can be built and run. No
candidate or live acceptance claim is made. After D-ADV-014 is fixed, rerun the
focused maintenance/source checks, then the complete candidate matrix, and
only then the required LLM-driven live-dev gate.

### Source remediation update — D-ADV-014

| Finding | Current source disposition | Source evidence | Remaining acceptance gate |
| --- | --- | --- | --- |
| D-ADV-014 — incomplete maintenance v17 schema check | **Resolved in source** | The single `_REQUIRED_COLUMNS["clarification_bindings"]` maintenance contract now requires `plan_content_digest`, `plan_approval_handle`, `plan_view_content_digest`, and `plan_view_source_sequence`. `tests/test_v17_maintenance_schema.py` proves healthy v17 health and, for each omitted relation field, reports `healthy=false` / `checks.schema=false` then rejects an action with the safe `maintenance_precondition_failed` code. | Candidate maintenance/migration evidence must still prove the forward migration and health contract in the exact package. |

The source defect in D-ADV-014 is therefore cleared without a reset,
downgrade, compatibility alias, candidate execution, or live-dev claim. Phase
D remains unaccepted pending D-ADV-013's exact-candidate gate and the required
candidate/live evidence.

## Final bounded source review — 2026-08-29

The final review rechecked D-ADV-014 and performed a regression sanity pass
against D-ADV-001 through D-ADV-014. The maintenance contract now requires all
four v17 relation columns on `clarification_bindings`:
`plan_content_digest`, `plan_approval_handle`, `plan_view_content_digest`, and
`plan_view_source_sequence`. A nominal v17 store passes health; each individual
missing-column fixture fails `checks.schema` and `healthy`, and maintenance
checkpoint rejects the action before opening a writable connection with the
safe `maintenance_precondition_failed` diagnostic. Migration 17 remains
forward-only and the existing migration list/validation stays aligned.

Focused source suites passed without candidate or live execution:

```text
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q \
  tests/test_v17_maintenance_schema.py \
  tests/test_decision_aggregate.py \
  tests/test_v12_compatibility.py \
  tests/test_public_mcp_first_call_conformance.py \
  tests/test_semantic_registry.py
22 passed, 10 subtests passed
```

No source-level P0/P1 finding remains across D-ADV-001 through D-ADV-014.
Source review is therefore **clear**. This does not promote candidate or live
status: D-ADV-013 remains open until the exact candidate package is built and
qualified, followed by the required real LLM-driven live-dev gate.

## Candidate requalification — 2026-08-29 (D-ADV-013)

The package-preflight blocker was remediated without weakening candidate
provenance. The three empty generated `__pycache__` directories under the
plugin/tests scopes were removed after an exact inventory; no `.pyc` or `.pyo`
files were present. They originated from earlier checkout Python/pytest
inspection. The qualification harness and command now set
`PYTHONDONTWRITEBYTECODE=1` and use `python3 -B`; post-run inventory remained
empty.

The repository-supported candidate builder staged a temporary content-addressed
`1.12.1` package. The candidate MCP process removed `PYTHONPATH` and
`CORTEX_SOURCE_MODE`, verified `parityVerified=true`, and matched build/source
digest `sha256:4d678e846ba23f4fa00f75dab7c9ca8e34a38267257f145d02843ba8ad5e191e`.
No stable profile, installed plugin, or live-dev session was touched.

```text
PYTHONDONTWRITEBYTECODE=1 python3 -B -m pytest -q tests/test_phase_d_decision_qualification.py
5 passed, 5 failed, 0 skipped, 0 collection errors
```

D-ADV-013 is therefore **not passed**: the candidate was launched and the
stdio boundary was exercised, but five architectural invariants failed:

| Candidate failure | Sanitized evidence | Required root remediation |
| --- | --- | --- |
| Changed-response conflict | After an exact clarification replay, a changed response returned generic `ledger_error` instead of a semantic conflict code. | Preserve aggregate conflict codes through public MCP error translation. |
| Concurrent open | One concurrent candidate process returned retryable `storage_unavailable` during an identical first-call open. | Make transaction/busy handling deterministic for same logical opens. |
| Plan relation | Semantic `publish_plan` returned no ready `approval_view`; plan review could not obtain the immutable relation. | Bind and expose the server-owned plan/view relation before review. |
| Steering supersession | The relation existed only as a canonical internal ID; public evidence lacked a bounded typed supersession relation. | Project a typed supersession relation without making canonical IDs callable. |
| Stale binding | Recording a binding after a steering revision returned generic `ledger_error` instead of the advertised stale code. | Preserve stale-binding classification through MCP error translation. |

The five passing cases are candidate evidence only for provenance/catalogue,
clarification persistence, record concurrency after a successful open, restart
replay, steering revision/replay, and advisory/no-scheduling side effects.
They do not promote any matrix row to candidate-verified. No live-dev claim is
made. D-ADV-013 remains open until the same exact-candidate command passes with
zero failures and zero skips.

## Candidate root-cause architecture review — 2026-08-29

The bounded review of D-CAND-001 through D-CAND-005 confirms four shared
architectural roots rather than five isolated defects: semantic error-code
closure; contention-safe receipt liveness; publication-to-readiness capability
closure; and typed relation-edge projection. The complete mapping, exact source
boundaries, and acceptance criteria are maintained in
[phase-d-candidate-root-cause.md](phase-d-candidate-root-cause.md). This review
did not modify production/tests or rerun candidate/live; D-ADV-013 remains
blocked until the complete candidate gate passes.

## Candidate remediation re-review — 2026-08-29

The source remediation was rechecked against the four roots in
[phase-d-candidate-root-cause.md](phase-d-candidate-root-cause.md). The central
receipt executor is structurally convergent and preserves exact-request
conflicts, and MCP error details remain safely whitelisted. Source clearance is
**withheld** because three P1 defects remain:

1. The registry is not exhaustive: typed store errors
   `clarification_binding_consumed`, `outcome_item_not_found`,
   `outcome_item_stale`, and `outcome_assignment_conflict` are not registered,
   while operation metadata still names generic aliases. Known typed failures
   can therefore fall back to `ledger_error`.
2. `publish_plan` commits the report before `_with_human_view` materializes the
   view and mints the approval handle. Readiness/relation delivery is not
   atomic with publication and remains vulnerable to intervening chronology.
3. Steering output still advertises canonical `decision_id`, `task_id`, and
   `subject_id`, and publication output uses a permissive untyped envelope
   rather than an explicitly advertised ready approval relation.

The complete evidence, required source tests, and exact affected files are in
the linked root-cause document. No production/tests were changed and no
candidate/live-dev run was performed. D-ADV-013 remains blocked.

## Latest R1–R4 source re-review — 2026-08-29

R1 is now source-supported: the registry covers the literal public runtime
error codes and operation metadata uses canonical codes; safe MCP details remain
allow-listed. R2 is source-supported by the centralized bounded receipt retry,
exact digest admission, and read-only reconciliation path. No P0 was found.

Two P1 blockers remain. First, `_ready_plan_review_relation` rechecks
`MAX(timeline.sequence)` during replay and `open_plan_review`; an unrelated
later event can make an already persisted immutable publication relation appear
unavailable, which is latest-state reconstruction. Second, the public
`publish_plan` result still permits canonical report IDs through permissive
`report`/`handles` schemas and `_compact_report`, despite having a typed
approval relation. Source clearance remains withheld until these are corrected
and covered by source tests, then proven at the candidate and live gates.

### Candidate-root P1 source remediation — 2026-08-29

The four P1 defects from the candidate-root re-review are now source-remediated:
the registry covers every intentional public typed error without alias metadata;
the receipt executor has deterministic forced-busy/lost-response/restart
coverage; plan report, immutable rendered revision, ready relation, and report
operation commit together; and family/public publication outputs are closed and
compact. The detailed matrix and exact tests are maintained in
[phase-d-candidate-root-cause.md](phase-d-candidate-root-cause.md) and
[phase-d-remediation.md](phase-d-remediation.md). This is **not** a candidate
or live-dev result: D-ADV-013's candidate gate and the required live check
remain open.

## Post-remediation adversarial verification — 2026-08-29

The subsequent source-remediation claim was checked against the current
implementation rather than accepted as evidence. R1 and R2 remain
source-supported. R3 and R4 are not cleared: plan relation replay/open still
depends on mutable `MAX(timeline.sequence)`, and the public plan publication
result still allows canonical IDs through permissive nested `report`/`handles`
schemas. The family `decision_output` schema also still advertises
`decision.decision_id`, despite the runtime compact projection omitting it.
These are P1 blockers, not candidate-only uncertainty. No
production/tests were changed or run, and no candidate/live-dev execution was
performed.

## P1 boundary closure verification — 2026-08-29

The two P1 defects described in the preceding verification are now corrected
and source-tested. Approval relation validity no longer compares an immutable
snapshot to global task chronology: only its task/project/type anchors,
immutable digests/source sequence, opaque handle, and consumption state are
relevant. The focused aggregate regression inserts later unrelated governance
and initiative timeline events before open replay and record, then proves the
same relation remains valid; a changed view produces a distinct relation and
binding.

`publish_plan` now emits a recursively closed compact report and approval
relation, with closed typed handles. Canonical decision/task/subject/report/
delegation/operation IDs are absent from that public result, and the actual
tools/list schema is asserted against the emitted stdio structure. This is
source evidence only: candidate and live-dev gates remain unrun and unclaimed.

## Final R3/R4 source re-review — 2026-08-29

This bounded read-only re-review checked the newest production source rather
than relying on the remediation narrative. R3 is source-cleared: relation
validity no longer reads global/current timeline state, unrelated chronology
preserves open replay and record, and a materially changed rendered view is
covered by a distinct server-issued relation/binding. Publication continues
to materialize the report, projection metadata, digests, approval relation,
and receipt within one durable command boundary with rollback on failure.

R4 is source-cleared: all six family decision outputs and the complete
`publish_plan` output are recursively closed; the plan report, approval view,
and nested handles contain only compact refs/digests/status/sequence and the
opaque approval handle. Static tools/list-to-runtime schema validation passes,
and `_handles` consumes compact emitted refs rather than exposing canonical
IDs. R1/R2 sanity checks remain clear, with no new P0/P1 findings. This is
source evidence only; candidate D-ADV-013 and the required LLM-driven live-dev
gate remain open and unrun.

## Fresh candidate requalification — 2026-08-29

The package-hygiene preflight was clean: no generated bytecode artifacts were
found under the plugin or test scopes. A fresh content-addressed `1.12.1`
candidate was staged and exercised through real stdio MCP with
`PYTHONDONTWRITEBYTECODE=1`, `python3 -B`, checkout `PYTHONPATH` removed,
source mode removed, and `parityVerified=true`. The sanitized build/source
digest was `sha256:c84a4ae3c20534694e55de9749081bb50291773457963cd088ad8da2026d3e7a`.
No installed profile or live-dev session was touched.

```text
PYTHONDONTWRITEBYTECODE=1 python3 -B -m pytest -q tests/test_phase_d_decision_qualification.py
4 passed, 6 failed, 0 skipped, 0 collection errors
```

D-ADV-013 remains **open**. The failures are qualification-contract
alignment failures after source remediation, not grounds to weaken the gate:

- changed clarification and plan-review inputs now return canonical
  `command_conflict`, while the test still accepts only retired family-specific
  aliases;
- all three plan outcome cases reach first record and exact replay, but remain
  red because their changed-outcome assertions use the stale alias set;
- steering completes revision/replay, but the test expects canonical nested
  decision fields absent from the current bounded result projection; the
  bounded decision object itself is present with compact refs and the typed
  supersession relation;
- stale recording returns safe `command_conflict` and cross-project recording
  returns safe `clarification_binding_not_found`, while the test sets omit those
  canonical classes.

The candidate did prove catalogue/provenance, concurrent open/record
convergence, restart replay, and advisory/no-scheduling behavior. It did not
prove the complete six-operation matrix. Reconcile the qualification contract
with the advertised canonical result/error schemas, then rerun the entire
candidate suite. Live-dev remains prohibited until it is fully green.

## Corrected strict candidate requalification — 2026-08-29

The qualification-only contract corrections were applied without production
changes or allowlist broadening. A fresh content-addressed `1.12.1` candidate
was executed through real stdio MCP with bytecode disabled, checkout imports
removed, source mode removed, and `parityVerified=true`.

```text
PYTHONDONTWRITEBYTECODE=1 python3 -B -m pytest -q tests/test_phase_d_decision_qualification.py
9 passed, 1 failed, 0 skipped, 0 collection errors
```

The sole failure is now a genuine invariant failure rather than an unhandled
thread warning: one of two real candidate processes returned retryable
`storage_unavailable` while issuing identical concurrent
`open_clarification` calls. All other family operations, canonical conflict
and stale error classes, plan relation/outcomes, steering supersession
projection, restart/replay, provenance, safety, and no-scheduling assertions
passed. This is a transaction/busy-handling defect in the production path.
D-ADV-013 remains open; no live-dev execution is permitted until the complete
candidate suite passes with zero failures and zero skips.

## Candidate contention root-cause review — 2026-08-29

The strict candidate requalification now has one real failure: two independent
stdio processes issue the same `open_clarification`, and one receives
`storage_unavailable`. This is recorded as **D-CAND-006 (P1)**, not as a stale
qualification assertion. The source call path reaches
`V12Store.for_task_ref -> _task_id_for_ref_suffix -> _verify_known_task` before
`DecisionAggregate.open -> run_command_receipt_resolved`. `_verify_known_task`
enters `BEGIN IMMEDIATE` and performs automatic migrations/backfill on every
compact task resolution; WAL setup and sidecar finalization also occur outside
the central receipt runner. Thus lock/setup contention can fail before receipt
identity exists and cannot be reconciled by the receipt executor. The 15-second
SQLite/busy timeout is additionally inconsistent with `_write`'s 0.8-second
budget, and sidecar cleanup can mask a primary contention error with
`storage_unavailable`.

This review did not run candidate/live-dev or edit production/tests. Source
clearance for the decision architecture remains withheld at the candidate
gate. The root fix is one generic process-independent shard-admission/schema
readiness gate with one monotonic contention budget for every public command,
automatic but once-per-shard migration, preserved exact receipt reconciliation,
and non-masking fail-closed cleanup. Required source and exact-candidate tests
are recorded in [phase-d-candidate-root-cause.md](phase-d-candidate-root-cause.md).

## D-CAND-006 remediation re-review — 2026-08-29

The proposed generic admission gate improves task-shard, bootstrap, read,
write, and known-anchor verification paths, but source clearance is withheld
at **P1**. `for_record_ref` still accesses the shared locator database through
`_record_locator_matches` before the gate; legacy locator repair and
post-commit `_sync_record_locators` also bypass the same policy. A public
`resolve_record_ref` can run after `_task_store` has reset the deadline.

The storage deadline therefore is not one public-command budget. `_write_once`
can oversleep its remaining deadline, and journal setup uses message matching
instead of the canonical SQLite busy/locked code. Forward-only automatic
migrations, fail-closed schema validation, and primary-error preservation in
the yielded connection path are positive evidence, but they do not cover
these bypasses or prove two-process public convergence. The focused synthetic
test does not exercise them. D-CAND-006 remains blocked pending the generic
source tests and a clean exact-candidate run; candidate/live-dev were not run.

## D-CAND-006 final bounded source re-review — 2026-08-29

The shared-admission remediation is **not source-cleared (P1)**. Task-shard
lookup, bootstrap, known-anchor verification, reads, writes, WAL/pragmas, and
receipt entry now use `_with_storage_admission`, with the main ledger timeout
derived from its deadline and primary yielded-operation errors protected from
sidecar cleanup masking. However, `for_record_ref` first reads
`record-locators.db` through a direct connection; legacy locator repair and
post-commit locator refresh also bypass the gate. Some `resolve_record_ref`
calls occur after `_task_store` restores the deadline.

The deadline is therefore not end-to-end: `_write_once` can sleep beyond the
remaining budget, and journal setup identifies contention by exception text
rather than the SQLite busy/locked code. Automatic forward migrations and
fail-closed schema validation remain intact, but the focused synthetic test
does not prove two independent stdio processes converge through all public
task/record-resolution paths. D-CAND-006 and D-ADV-013 remain blocked; no
candidate/live-dev execution was performed.

## D-CAND-006 ContextVar final bounded adjudication — 2026-08-29

The latest source review confirms one inherited monotonic admission deadline
through locator/task/record resolution, shard/bootstrap readiness,
migration/backfill, WAL/pragmas, anchor checks, reads, writes, and receipt
transactions. All busy waits are capped; busy/locked classification uses the
SQLite numeric error code; and sidecar cleanup does not replace a primary
operation error. Automatic forward migrations and fail-closed schema health
remain supported.

One **P1** remains at the public legacy `_mutation` boundary: `_write` returns
and resets its admission context before `_sync_record_locators` runs. That
derived refresh can fail under a fresh budget after canonical rows and the
receipt have committed, exposing a misleading command failure. The shared
command boundary must encompass the refresh or make it non-authoritative and
non-failing. This is an architectural scope issue, not a request for another
operation-specific retry.

Two independent candidate stdio processes are sufficient black-box candidate
evidence once the exact suite is green. Source clearance still requires a
bounded equivalent using two source `cortex.py` processes through MCP; current
source tests are in-process/direct-store. Keep the candidate and source gates
separate, then require zero candidate failures/skips before live-dev. No
candidate/live-dev execution was performed.

## D-CAND-006 ContextVar final bounded adjudication — 2026-08-29 (superseding addendum)

The latest source review confirms one inherited monotonic admission deadline
through locator/task/record resolution, shard/bootstrap readiness,
migration/backfill, WAL/pragmas, anchor checks, reads, writes, and receipt
transactions. All busy waits are capped; busy/locked classification uses the
SQLite numeric error code; and sidecar cleanup does not replace a primary
operation error. Automatic forward migrations and fail-closed schema health
remain supported.

One **P1** remains at the public legacy `_mutation` boundary: `_write` returns
and resets its admission context before `_sync_record_locators` runs. That
derived refresh can fail under a fresh budget after canonical rows and the
receipt have committed, exposing a misleading command failure. The shared
command boundary must encompass the refresh or make it non-authoritative and
non-failing. This is an architectural scope issue, not a request for another
operation-specific retry.

Two independent candidate stdio processes are sufficient black-box candidate
evidence once the exact suite is green. Source clearance still requires a
bounded equivalent using two source `cortex.py` processes through MCP; current
source tests are in-process/direct-store. Keep the candidate and source gates
separate, then require zero candidate failures/skips before live-dev. No
candidate/live-dev execution was performed.

## D-CAND-006 ContextVar remediation re-review — 2026-08-29

The latest source closes the previously identified locator/deadline gaps.
`for_task_ref`/`for_record_ref` establish a ContextVar deadline; locator lookup,
legacy repair, shard readiness, WAL/pragmas, anchor checks, reads, writes, and
receipt admission inherit it. All waits are capped, SQLite busy detection uses
the numeric SQLite code, and sidecar cleanup cannot replace a primary error
from the yielded operation. Forward-only migrations and fail-closed schema
validation remain intact. No source P0/P1 implementation defect was found.

Bounded scope caveat: the public legacy `_mutation` path resets the admission
context when `_write` returns before derived `_sync_record_locators` runs.
This remains a P1 end-to-end command-boundary issue: a refresh failure can be
observed after canonical commit under a new budget. The deadline must span
that work, or the refresh must be explicitly non-authoritative and
non-failing.

The evidence boundary is still important. The Phase D exact-candidate test
already launches two independent real stdio candidate processes and is
sufficient black-box evidence for D-CAND-006 after a green current-package
run. The source suites have no equivalent two-process source-mode MCP test;
their in-process `serve_stdio` and direct receipt tests do not cover the full
process boundary. A separate bounded source stdio regression remains required
for source clearance, while candidate qualification remains the packaging
gate. D-CAND-006 is therefore source-supported but not source-cleared; no
candidate/live-dev execution was performed.

## D-CAND-006 Final locator-authority and source-stdio review — 2026-08-29

The latest `_mutation` boundary now wraps the complete legacy mutation and
makes post-commit locator refresh best-effort. A refresh failure therefore
cannot revoke or misreport a committed canonical result. The refresh itself
is bounded and idempotent because it rebuilds derived rows from canonical
shard records; normal indexed resolution still verifies the canonical row.

Source clearance remains **P1-blocked**. In `_for_record_ref_once`, a valid
canonical result found by the fallback shard scan is followed by synchronous
`_sync_record_locators()`. If that derived repair fails, the canonical record
is not returned. `_record_locator_matches` also turns sidecar schema/read
failures into `storage_unavailable` instead of treating the sidecar as
unavailable and falling back. A non-authoritative locator must never block a
verified canonical record; repair must be best-effort, while canonical shard
schema/security failures remain fail-closed.

The new source stdio test genuinely launches two independent source
`cortex.py` processes, performs JSON-RPC initialization and `tools/call`, and
exercises compact task resolution, shard readiness, and identical open
convergence. It does not assert the command-receipt row, changed-input
conflict, or record-locator fallback/repair. Add those bounded source MCP
assertions before source clearance; the exact-candidate gate remains separate.
No candidate/live-dev execution was performed.

## D-CAND-006 Final bounded locator-authority adjudication — 2026-08-29

The latest source fixes the earlier post-commit mutation failure: `_mutation`
now owns the full legacy lifecycle and `_refresh_record_locators_after_commit`
is bounded/best-effort, so derived-index failure cannot revoke a committed
canonical result. Indexed record lookup verifies the canonical shard row.

Two P1 paths still violate the non-authoritative locator invariant. During
`_for_record_ref_once`, a canonical record found by the fallback shard scan is
followed by synchronous `_sync_record_locators`; a repair failure prevents the
verified record from being returned. Also, `_record_locator_matches` maps
sidecar schema/read failures to `storage_unavailable` rather than treating the
accelerator as unavailable and falling back. `_bootstrap_once` performs an
initial sidecar rebuild after canonical schema commit and can likewise abort
store construction. Sidecar repair must be best-effort and reconstructible;
canonical shard schema/security errors must remain fail-closed.

The source stdio test is a genuine two-process `cortex.py` MCP test covering
initialization, compact task resolution, shard readiness, and identical open
convergence. It does not yet assert the command-receipt row, changed-input
conflict, or record-locator fallback/repair. Source clearance remains blocked
until these behaviors are covered and pass; no candidate/live-dev execution
was performed.
## D-CAND-006 final connection-lifetime lease review — 2026-08-29

Adversarial source checks passed for the active path: unsafe symlink and
directory sidecars fail closed; lease reentrancy, exception release, process
exit recovery, and shard locality pass; the source MCP stress ran 80
simultaneous two-process pairs without nonzero child exits, forced
termination, hidden EOF, split bindings, or duplicate open receipts.  The
full focused source run passed (`38 passed, 96 subtests`), and the dedicated
stdio scenario proved one binding/open receipt, one record mutation/receipt,
and changed-input `command_conflict`.

A P1 remains.  Static source inspection finds the dormant
`_secure_sqlite_sidecar` path invoking `os.fchmod` on live WAL/SHM.  Although
the active connection path now calls the non-mutating validator, this leaves
a callable source path that violates the no-Cortex-mutation invariant.  This
review therefore does not grant source clearance; candidate and live-dev
remain unrun.
## D-CAND-006 final post-removal clearance check — 2026-08-29

Read-only review confirms that `_secure_sqlite_sidecar` is gone and current
WAL/SHM handling is validation-only.  The 80 simultaneous source-MCP pairs
remain green, with no observed nonzero exit, forced termination, hidden EOF,
split binding, or duplicate receipt; focused source checks also pass.

The source gate remains **P1-blocked** because the AST test is bypassable by
computed path construction, call aliasing, or indirection.  This is a
qualification-control defect even though the present implementation has no
such path.  No candidate or live-dev result is promoted.
## D-CAND-006 final filesystem-boundary review — 2026-08-29

The active source behavior is green: no current store-sidecar mutator was
found, the observer runs in the actual exec'd MCP children, and the 80-pair
stress has no observed crash, hidden EOF, forced termination, split binding,
or duplicate receipt.

Adversarial bypass testing found a P1 in the proof boundary.  The policy
accepts `os` assignment aliases and aliased `pathlib.Path` mutation, scans
only the immediate package directory, while the observer lacks
`os.write`/`os.ftruncate` and equivalent low-level paths.
This review therefore does not grant source clearance; candidate and
live-dev remain unrun.
## D-CAND-006 final filesystem-policy adversarial review — 2026-08-29

Current implementation checks are green and the child observer sees the
required path and FD mutation primitives.  Additional adversarial probes
found a P1 proof-boundary gap: `os.__dict__["unlink"](...)` and callback or
helper-returned mutators are accepted by the static policy.  Source clearance
is therefore withheld; candidate and live-dev remain unrun.
## D-CAND-006 final callable-flow review — 2026-08-29

Adversarial fixtures for subscript/dynamic lookup, return/yield, partial,
callback, container, attribute, decorator, closure, nested helper, and FD
dup/write-family paths now fail closed.  The remaining helper-returned
`pathlib.Path` constructor flow bypasses the checker and is a P1 source-proof
gap.  Source clearance is withheld; candidate/live-dev remain unrun.
## D-CAND-006 final declared-scope clearance — 2026-08-29

The final adversarial fixtures for returned `pathlib.Path` constructors and
returned `pathlib`/`os`/`shutil` modules now fail closed across direct,
assigned, one-hop, and two-hop flows.  All previously declared subscript,
callable-storage, nested-scope, and FD-dup/write-family checks pass.

D-CAND-006 is **source-cleared within the declared Python/runtime scope**.
No candidate or live-dev result is promoted by this source-only review.

## Final exact-candidate adversarial verification — 2026-08-29

The fresh content-addressed `1.12.1+codex.sha256.eb691a9a49377dcc` candidate
passed marketplace/release validation and reported `parityVerified=true` from
its real stdio MCP server. The candidate harness removed checkout
`PYTHONPATH` and `CORTEX_SOURCE_MODE`, used isolated `HOME`/`CODEX_HOME`, and
ran with bytecode disabled. The complete Phase D suite passed **11/11 with
zero failures, skips, or errors**.

The 80-pair candidate stress passed with two independent children per isolated
pair. All pairs produced one binding/open receipt, one record mutation/receipt,
an exact replay, and a changed-input `command_conflict`. Exit/stderr-aware
checks found no duplicate mutation, nonzero exit, forced termination, hidden
EOF, SIGBUS, stderr, or Python-side WAL/SHM sidecar. D-ADV-013 is therefore
closed for the exact-candidate Decision gate. The focused LLM-driven live-dev
gate remains unrun and is the next acceptance step.
