# Phase D candidate findings: root-cause architecture map

Status: blocking architectural analysis of the candidate failures recorded on
2026-08-29. This document is a source-guided remediation contract, not a
candidate or live-dev result. It contains no private logs, credentials, or
opaque runtime identifiers.

## Evidence boundary

The candidate qualification record in
`docs/architecture/phase-d-qualification.md` initially reported five
qualification failures:

| Finding | Candidate symptom | Affected boundary |
| --- | --- | --- |
| D-CAND-001 | Changed response after an exact replay returns generic `ledger_error` instead of a semantic conflict. | Store/domain error → service adapter → MCP error projection |
| D-CAND-002 | Concurrent identical opens can return retryable `storage_unavailable` under SQLite contention. | Receipt transaction executor and cross-process SQLite liveness |
| D-CAND-003 | `publish_plan` does not expose a ready `approval_view`, so review cannot obtain its immutable relation. | Atomic publication → human-view projection → approval relation producer |
| D-CAND-004 | Steering supersession exists internally but public evidence exposes only a canonical internal decision ID, not a bounded typed relation. | Decision result projection and output schema |
| D-CAND-005 | A stale binding returns generic `ledger_error` instead of the advertised stale semantic error. | Store/domain error → service adapter → MCP error projection |

These original five are not five independent defects. D-CAND-001 and D-CAND-005 are the
same error-contract failure; D-CAND-002 is the command-liveness invariant for
the same receipt boundary; D-CAND-003 is a producer/consumer capability
closure failure; and D-CAND-004 is the corresponding typed-evidence projection
failure.

## Shared-root mapping

| Root | Findings covered | Current source evidence | Architectural correction |
| --- | --- | --- | --- |
| R1 — no single semantic error taxonomy and projection | D-CAND-001, D-CAND-005 | `v12_store.py` produces typed `command_conflict` and `clarification_binding_stale` errors, while `mcp_api._PUBLIC_SERVICE_CODES` admits only a separate hand-maintained set. `domain_api` also converts aggregate `RuntimeError` paths into generic `clarification_binding_not_found`; unknown codes fall through `_service_failure` to `ledger_error`. | Define one registry-owned error taxonomy with code, public message, retryability, safe details, and recovery action. Domain Kernel/store exceptions must carry only taxonomy members; service and MCP must project the same code without a second allow-list. Unknown/untyped exceptions remain a sanitized internal fault. |
| R2 — receipt executor lacks deterministic contention reconciliation | D-CAND-002 | `_write` uses one `BEGIN IMMEDIATE` attempt after SQLite's timeout; `_connection` retries only initial WAL mode setup. Cross-process contention can surface before the logical receipt is inspected. | Put bounded lock acquisition, same-slot request comparison, and read-only receipt reconciliation in the single command-receipt executor. On contention, retry the identical transaction within a bounded deadline; if a receipt appeared, return exact replay; if no receipt exists, return one typed retryable busy result. Never re-run mutation with a new identity or create a replacement binding. |
| R3 — publication readiness is not a closed producer capability | D-CAND-003 | `domain_api.publish_plan` calls `_publish`, which directly returns `publish_domain_report`; `_with_human_view`/`_ready_approval_view` are not part of that semantic publication path. The approval relation is therefore produced only by later read/projection paths. | Make semantic plan publication own a single durable readiness protocol: persist the immutable report, materialize/verify its exact view, mint the approval relation, and return the typed relation in the same command result (or return an explicit non-ready state that `open_plan_review` cannot bypass). The relation must never depend on a later mutable/current projection lookup. |
| R4 — relation-bearing evidence lacks one typed public projection | D-CAND-004 | `_family_result` adds `decision_ref` for the recorded decision but leaves `supersedes_decision_id` as canonical evidence. `decision_output` describes the canonical field and has no bounded `supersedes_decision_ref` relation. | Centralize decision projection: every relation edge is converted to a same-task typed compact ref, with closed output schema and explicit relation semantics. Canonical IDs remain internal/non-callable. The supersession relation must be persisted atomically and returned in the same bounded decision evidence. |

## Smallest architectural remediation set

The minimum root change is four coordinated vertical-slice corrections, not
five case-specific patches:

1. **Registry-owned error contract.** Extend semantic registry metadata with the
   complete public error taxonomy and derive service/MCP messages, retry flags,
   safe detail fields, and recovery actions from it. Replace aggregate raw
   `RuntimeError` paths with typed domain errors. Add one projection function
   from Domain Kernel/store failure to the public MCP envelope.
2. **One contention-safe receipt executor.** Make every semantic command use
   the same lock-aware receipt runner. It must acquire the write transaction,
   normalize and compare the logical request, retry only the identical request
   under bounded SQLite contention, reconcile an already-created receipt, and
   atomically commit mutation plus receipt. The runner owns liveness and
   idempotency; individual commands must not invent retry loops.
3. **Closed publication/readiness protocol.** Treat a finalized plan's ready
   approval relation as a produced capability, not an incidental read view.
   Publication must atomically establish the report/revision and durable
   readiness relation, while the projection layer verifies file/view bytes
   before exposing readiness. `publish_plan` must return the typed relation
   needed by `open_plan_review`; `open_plan_review` must reject missing or stale
   readiness without selecting a current/latest substitute.
4. **Typed relation projection.** Introduce one bounded public projection for
   decision evidence and relation edges (including supersession). Apply it to
   all family results and derive the closed output schemas from the same
   relation vocabulary. This keeps server identity authoritative without
   leaking canonical IDs or forcing reconstruction.

These four roots preserve orchestration behavior: the coordinator still asks
questions, interprets answers, schedules workers, chooses governance depth,
selects rework, and synthesizes results. The backend only makes identity,
transaction, relation, and safe-error guarantees deterministic.

## Acceptance criteria

### Error taxonomy and projection

- A changed response after exact replay returns the advertised semantic
  conflict code in `structuredContent.error`, with the fixed message/action
  and no traceback or private detail.
- A binding made stale by a committed effective-contract revision returns the
  advertised stale code through the same path.
- Every registry-declared error is reachable through store/domain/service/MCP
  projection; no family-specific hand-maintained allow-list can downgrade a
  known code to `ledger_error`.
- An untyped implementation fault still becomes one safe internal error and
  never becomes a false conflict, stale, success, or retry instruction.

### Contention, receipt, and recovery

- Concurrent identical opens across real candidate processes produce one
  committed binding/receipt and exact replay for all other callers, without a
  first-call busy error within the bounded contention budget.
- A changed request for an occupied logical slot returns conflict and creates
  no second mutation; an ambiguous transport result is reconciled by the
  original slot/handle only.
- Rollback removes binding, decision, timeline, effective revision, and
  receipt together when any command step fails.
- Process restart and lost-response recovery return the original receipt and
  binding, never a replacement identity.

### Plan publication and approval relation

- A successful `publish_plan` result includes a ready, typed approval relation
  containing the exact plan digest, approval handle, view digest, and source
  sequence; all values are server-produced and byte-copyable.
- If view materialization is unavailable, publication returns an explicit
  non-ready state and no `open_plan_review` call can silently create or infer a
  relation.
- `open_plan_review` and approve/request_revision/cancel consume only the
  persisted relation for that binding. Competing newer views cannot redirect
  or invalidate the bound relation unless the bound artifact itself is no
  longer valid.
- Publication/report/relation state is atomic at the durable command boundary,
  and exact replay returns the original result without rematerializing a new
  relation.

### Typed supersession evidence

- A steering record returns a closed decision evidence object containing the
  exact typed `supersedes_decision_ref` (or an equivalent explicitly named
  relation), anchored to the same task.
- The public schema accepts and emits only bounded compact relation refs;
  canonical IDs remain absent from callable/public relation fields.
- Same-input replay returns the same supersession evidence; a changed
  supersession target conflicts without a second effective-contract revision.

## Required source and candidate tests

| Layer | Required tests |
| --- | --- |
| Source/unit | Registry error-taxonomy completeness; every typed store/domain error projects unchanged; unknown exceptions sanitize; relation projection recursively closed and canonical-ID-free. |
| Source/integration | Cross-process lock contention with one receipt; changed request conflict; rollback across binding/decision/timeline/revision/receipt; process restart and read-only reconciliation. |
| Candidate stdio | Advertised error code for changed response and stale binding; concurrent opens; `publish_plan` ready relation; all three plan outcomes against the persisted relation; typed steering supersession evidence; zero mutation replays or hidden tool errors. |
| Live | Real LLM-driven coordinator flow observes these same errors/results and worker events; transport only sends text/keys and never classifies, answers, approves, or recovers autonomously. |

## Parity-matrix extension

The existing parity matrix covered transactional writes, relation checks, and
candidate/live evidence separately. It did not make these cross-cutting
invariants explicit. The matrix must now treat the following as first-class
preservation capabilities: semantic error-code closure, contention-safe command
liveness, publication-to-readiness capability closure, and typed relation-edge
projection. A source pass is not candidate acceptance, and a candidate pass is
not live acceptance.

## Bounded remediation re-review — 2026-08-29

This re-review inspected the changed source and focused source-test surfaces;
it did not run candidate/live-dev and did not modify production or tests. The
remediation is **not source-cleared** because three P1 boundary defects remain.

| Root | Disposition | Exact evidence | Required correction |
| --- | --- | --- | --- |
| R1 — registry-owned error projection | **Blocked (P1)** | `semantic_registry.py::error_contract` is the MCP fallback, and `_safe_details` correctly whitelists bounded fields. However, typed store codes `clarification_binding_consumed`, `outcome_item_not_found`, `outcome_item_stale`, and `outcome_assignment_conflict` are emitted by `v12_store.py` but absent from `public_error_codes()`. The operation `safe_errors` tuples also retain generic aliases such as `binding_stale` and `decision_conflict`, so registry metadata is not a complete canonical taxonomy. These known typed failures can still become generic `ledger_error`. | Register every public typed code once, remove alias-only operation metadata, and add a source assertion that every public store/service code is registry-covered while only genuinely untyped faults use `ledger_error`. |
| R2 — contention-safe receipts | **Source evidence supports the design; no new P1 found** | `v12_store.py::_write` centrally bounds lock acquisition/retries; both receipt runners perform read-only exact-slot reconciliation after `storage_busy`; the transaction checks request digest before mutation and inserts mutation plus receipt in one commit. The existing cross-process source test covers one receipt and one mutation. | Preserve this single executor. Add a forced-busy/lost-response source test proving the retry path itself, changed-input conflict, and no replacement mutation; candidate evidence remains required. |
| R3 — publication/readiness closure | **Blocked (P1)** | `domain_api._publish` first commits `store.publish_domain_report`, then calls `legacy._with_human_view`. `_with_human_view` invokes `human_view`/`materialize_task` and `ready_approval_handle` in later operations. The report, projection file, approval handle, and returned `approval_view` are therefore not one atomic publication transaction; concurrent timeline activity can turn a successful publication into an unavailable relation. | Move readiness verification and relation persistence behind one publication command/receipt boundary, or durably return an explicit pending state that cannot be presented as a successful ready publication. Do not rely on a post-commit latest-sequence lookup. |
| R4 — typed relation projection | **Blocked (P1)** | `_family_result` removes only `supersedes_decision_id` and adds `relations.supersedes_decision_ref`, but `public_contracts.py::decision_output` still advertises canonical `decision_id`, `task_id`, and `subject_id` in the steering result. Publication uses a permissive `compact_output` with `additionalProperties: true`, so the ready approval relation is not a typed advertised output for `publish_plan`. | Make all public relation-bearing result schemas closed and compact; keep canonical IDs internal/non-callable and expose only typed refs. Advertise the typed ready approval relation on `publish_plan` explicitly. |

### Safety and reconstruction sanity check

The MCP detail projection remains bounded: `_safe_details` admits only fixed
path/field/expected/reason/retry scalars, and fixed registry messages/actions do
not echo exception text. Record-time plan decisions validate only the persisted
binding relation and approval handle; they do not select a newest/current view.
Those are positive source properties, but they do not repair the non-atomic
publication producer or the public schema leaks above. Source clearance is
therefore withheld; D-ADV-013 remains a candidate/live gate after remediation.

## P1 remediation update — 2026-08-29

The four P1 boundary defects identified by the bounded re-review are now
corrected in source. This update is deliberately limited to source evidence:
no candidate package, live-dev session, installed profile, or qualification
fixture was run.

| Root | Source correction | Focused source evidence | Status |
| --- | --- | --- | --- |
| R1 | `semantic_registry.py` now has one canonical `ErrorSpec` for every intentionally typed store/domain failure, including consumed bindings and all outcome-item/assignment failures. `safe_errors` uses only these canonical codes and the registry validator rejects unknown/duplicate metadata. | `test_every_declared_or_raised_public_code_has_one_canonical_spec` scans public runtime raises and operation declarations, asserts one registry resolution per code, and asserts an unknown fault alone projects to `ledger_error`. | source-tested |
| R2 | The existing bounded `BEGIN IMMEDIATE` executor remains the sole contention mechanism. After exhausted busy acquisition it performs only exact-slot, read-only receipt reconciliation. | `test_forced_busy_lost_response_restart_reconciles_without_mutation` forces `storage_busy` after restart, proves original receipt replay/no second mutation, and proves changed digest conflict. The cross-process test remains the real-writer concurrency check. | source-tested |
| R3 | `V12Store.publish_domain_report` now renders the plan revision before commit, records ready projection metadata and digest, mints the exact approval handle/relation on the same connection, and returns it with the report operation. A rendering failure rolls report, report-operation, and relation rows back together. | `test_plan_publication_rolls_back_if_ready_relation_cannot_materialize` forces the atomic file step to fail and proves no canonical plan/report operation/approval handle commits; lifecycle replay checks prove one stable relation. | source-tested |
| R4 | Family decision projection whitelists compact decision/subject refs and typed supersession relations; canonical decision/task/subject IDs are omitted. `publish_plan` now advertises a closed approval-relation output schema on the actual public catalogue. | `test_steering_supersession_is_a_closed_compact_relation` validates the compact closed family shape; real MCP catalogue/first-call tests validate the advertised `publish_plan` schema and returned relation. | source-tested |

Candidate and live acceptance remain intentionally unclaimed. D-ADV-013 still
requires the exact candidate gate, followed by the required LLM-driven live
check; source passing cannot substitute for either.

## Follow-up P1 boundary closure — 2026-08-29

The follow-up review found and corrected two narrower boundary defects within
the earlier R3/R4 source remediation. The evidence remains source-only.

| Root | Follow-up correction | Focused evidence | Status |
| --- | --- | --- | --- |
| R3 | An approval relation is now valid solely through its immutable task/project/type anchors, report digest, view digest/source sequence, opaque handle, and consumption state. `_ready_plan_review_relation` no longer compares it with global `MAX(timeline.sequence)`, and approval-view reads do not apply global projection freshness. Later governance/initiative chronology cannot stale a presented relation; a materially distinct rendered view receives a distinct handle/review binding. | `test_plan_relation_ignores_later_unrelated_chronology_but_new_view_gets_new_relation` inserts later unrelated governance and initiative events before replay/record, proves the exact relation remains usable, then proves a changed view yields a new relation. | source-tested |
| R4 | `publish_plan` now returns a recursively closed compact report/ref/digest/relation result. It contains no report/task/delegation/operation canonical IDs, and its handles are explicitly typed and closed. Family decision schemas no longer advertise canonical decision/task/subject IDs absent from runtime output. | Real stdio first-call conformance asserts the advertised tools/list schema matches emitted publication structure and recursively rejects canonical identifier keys. | source-tested |

The same candidate and live gates remain required; this source update makes no
claim that either has been exercised.

## Latest bounded source re-review — 2026-08-29

The R1–R4 remediation was inspected again against the actual source and the
new focused test surfaces. R1 is now source-supported: all literal public
runtime error codes are registry-covered and operation declarations use
canonical entries; MCP details remain allow-listed and unknown faults alone
fall back to `ledger_error`. R2 is also source-supported: the shared receipt
runner retries bounded lock acquisition, reconciles an existing exact receipt
read-only, and checks request digests before mutation.

Source clearance is nevertheless **withheld** because two P1 defects remain:

| Root | Exact remaining defect | Required acceptance correction |
| --- | --- | --- |
| R3 — publication/readiness lifecycle | Initial plan publication now writes report, report operation, projection metadata, approval handle, digests, and source sequence under one database transaction, and a materialization failure rolls those canonical rows back. However, `_ready_plan_review_relation` still requires `projection_files.source_sequence == MAX(timeline.sequence)` on replay and on `open_plan_review`. Any later unrelated timeline event therefore makes an existing immutable relation appear unavailable, forcing mutable latest-state revalidation. | Replay and open must consume the persisted relation for the published report after chronology advances; validate the bound artifact's immutable digest/sequence, not global latest timeline state. Add post-publication timeline, restart, replay, and open-plan-review tests. Keep filesystem staging unreferenced until the canonical transaction is accepted or make the view bytes durable within the same atomic boundary. |
| R4 — closed public compact outputs | Family decision output now removes canonical decision/task/subject IDs and emits a typed supersession relation. However, `plan_publication_output.report` and `.handles` still use `additionalProperties: true`, while `_compact_report` supplies canonical `report_id`, `task_id`, `delegation_id`, and related internal fields. The real MCP structured result can therefore expose canonical IDs through the publication tool despite its new typed approval relation. | Replace permissive publication/report/handle schemas with closed compact projections containing only advertised fields and typed refs. Add a tools/list + successful MCP result assertion that no canonical `*_id` appears in public publication evidence. |

No P0 was found in this bounded review. The exact candidate and live-dev gates
remain unrun and must not be promoted from source evidence.

## Post-remediation adversarial verification — 2026-08-29

The later source-remediation claim was checked against the current files rather
than accepted as evidence. R1 remains clear at source level, and R2 remains
structurally supported by the central receipt runner plus focused tests. The
claim of complete R3/R4 remediation is not supported by the current source:

- `_ready_plan_review_relation` still compares a persisted view sequence with
  `MAX(timeline.sequence)`, so replay or `open_plan_review` can reject an
  otherwise valid immutable relation after unrelated chronology advances.
- `plan_publication_output.report` and `.handles` still permit arbitrary
  properties, and `_compact_report` still carries canonical report/task/
  delegation IDs into the structured publication result. The approval relation
  is typed, but the complete publication output is not a closed compact
  no-canonical-ID projection. In addition, `public_contracts.py::decision_output`
  still advertises `decision.decision_id`, so the family schema itself retains a
  canonical identifier even though `_family_result` omits it at runtime.

Accordingly, the source status remains **blocked (P1)** for R3 and R4. The
focused tests document the intended behavior but were not executed in this
read-only review; candidate and live-dev remain unrun.

## Final bounded R3/R4 source review — 2026-08-29

The latest remediation was rechecked against production source and focused
test surfaces. The prior R3/R4 blockers are now source-cleared. `_ready_plan_review_relation`
validates only the anchored plan/project relation, immutable report/view
digests and source sequence, readiness status, and persisted approval handle;
it no longer consults global or current timeline state. The aggregate regression
explicitly inserts unrelated chronology before open replay and record, then
proves the original relation remains usable; its changed-view fixture produces
a distinct handle and binding.

The complete `publish_plan` result is recursively closed: report, approval
view, handles, and nested approval relation all expose only compact refs,
digests, bounded status, sequence, and the opaque approval handle. The six
family decision output schemas are recursively closed and contain no canonical
decision/task/subject/report/delegation IDs. Static tools/list/runtime
conformance and centralized handle projection checks pass. R1/R2 remain
source-supported with no new P0/P1 findings. Candidate qualification and
LLM-driven live-dev remain mandatory and unrun; D-ADV-013 is still open.
## Fresh candidate requalification — 2026-08-29

This entry records the sanitized result of the full exact-candidate Phase D
run after R1–R4 source remediation. Package hygiene was clean before and after
execution: no `.pyc`, `.pyo`, or `__pycache__` artifacts were present under the
plugin or test scopes. The candidate was staged with the strict content-
addressed builder and run through real stdio MCP with bytecode disabled,
checkout imports removed, source mode removed, and `parityVerified=true`.

Command:

```text
PYTHONDONTWRITEBYTECODE=1 python3 -B -m pytest -q tests/test_phase_d_decision_qualification.py
```

Result: **4 passed, 6 failed, 0 skipped, 0 collection errors**. The candidate
build/source digest was
`sha256:c84a4ae3c20534694e55de9749081bb50291773457963cd088ad8da2026d3e7a`.
No installed profile or live-dev session was touched.

The six failures do not justify weakening the qualification suite. The
canonical semantic `command_conflict` class is now returned for changed
clarification/plan-review intent and stale recording, while the test's
accepted-code sets still name older family-specific aliases. Cross-project
recording returns the safe `clarification_binding_not_found` class, which is
also absent from that stale set. The steering transaction reaches revision and
replay, but the test expects canonical `decision_id` and
`supersedes_decision_id` fields inside the nested decision object. The current
bounded projection does return a nested decision object, but only with compact
refs and the typed supersession relation. The three plan outcome cases reach their
first record and exact replay before failing the changed-outcome assertion.

The candidate did pass catalogue/provenance, concurrent open/record
convergence, restart replay, and advisory/no-scheduling checks. The full
decision matrix remains unaccepted because the suite is red. The next action
is to reconcile the qualification assertions with the advertised canonical
error/result schemas and rerun the entire candidate suite; live-dev remains
blocked until it is green with zero skips.

## Strict assertion correction and rerun — 2026-08-29

The qualification assertions were corrected exactly to the advertised
canonical classes and bounded steering result: `command_conflict` for changed
intent, `clarification_binding_not_found` for cross-project lookup,
`clarification_binding_stale` for a stale binding, and compact
`relations.supersedes_decision_ref` with canonical IDs rejected. No production
code was changed.

The fresh exact-candidate run produced **9 passed, 1 failed, 0 skipped, 0
collection errors**. The remaining failure is the real concurrent-open
invariant: one of two candidate stdio processes received retryable
`storage_unavailable` during identical `open_clarification` calls. The test now
captures that thread result explicitly, so it cannot be hidden as a pytest
warning. All other six-operation, restart, plan, steering, safety, provenance,
and non-scheduling checks passed. The root cause is production transaction/
busy-handling behavior, not a qualification assertion. D-ADV-013 remains open
until concurrent identical opens complete without a first-call storage error.

## Bounded root-cause review: pre-receipt shard admission contention — 2026-08-29

This review is source-only. It did not patch production or tests, run the
candidate, run live-dev, refresh a cache, or touch an installed profile. The
finding is intentionally recorded as an architectural boundary defect rather
than as a recommendation to add another `open_clarification` retry.

### Finding

The sole candidate failure is **P1 / D-CAND-006**: the central semantic receipt
executor does not own the complete public command admission path. For an
`open_clarification` request, the path is:

```text
MCP tools/call
  -> domain_api.open_clarification
  -> v12_service._task_store
  -> V12Store.for_task_ref
       -> _task_id_for_ref_suffix (connection/read setup)
       -> _verify_known_task
            -> _connection + BEGIN IMMEDIATE
            -> migrations + timeline backfill + schema validation
  -> DecisionAggregate.open
  -> run_command_receipt_resolved
       -> _write + receipt/binding transaction
```

`V12Store.for_task_ref` performs a shard-wide scan and then invokes
`_verify_known_task` before `DecisionAggregate.open` is reached. The verifier
opens a writable transaction and runs all forward migrations/backfill checks
on every compact task-reference resolution. Therefore two independent stdio
processes can contend in `_verify_known_task` (or even in the preceding WAL
connection setup) before `run_command_receipt_resolved` has computed a logical
slot or can perform read-only receipt reconciliation. A busy/failure at this
boundary is consequently not covered by `_write`'s bounded policy.

There is a second determinism defect at the same boundary. `_connection`
configures SQLite with a 15-second native timeout and 15-second
`busy_timeout`, while `_write` declares a 0.8-second monotonic retry budget.
The native wait can therefore outlive the executor budget. In addition,
`_connection` always calls `_materialize_sidecars` from `finally`; a sidecar
filesystem error during cleanup can replace an original SQLite contention
exception with the public `storage_unavailable` class. This is a credible
explanation for the observed class, but no raw runtime stack is retained here,
so this document does not claim which specific sidecar operation failed in the
candidate process.

The earliest architectural failure is thus **pre-receipt shard admission**:
reference resolution, schema readiness, WAL setup, and transaction entry are
separate from the command executor. The receipt runner itself may be correct
once entered; that does not make the public operation reliable because the
operation can fail before entry.

### Smallest generic architectural remediation

1. Introduce one process-independent `open_shard`/schema-readiness gate used by
   every public operation before domain code. It must resolve the exact shard,
   validate the task anchor, and perform any required forward migration under a
   single centralized storage-admission policy. A current, validated schema
   must use read-only readiness checks; migrations remain automatic but are not
   repeated as a write transaction for every task reference.
2. Define one monotonic contention deadline for connection setup, WAL
   negotiation, schema migration, `BEGIN`, command mutation, and read-only
   reconciliation. Configure SQLite's native timeout from the remaining budget;
   do not nest a fixed 15-second SQLite wait under a shorter executor budget.
   The policy must be shared by all public commands and must not contain
   operation-specific sleeps, exception allowlists, or tool-name branches.
3. Keep receipt identity authoritative. If admission contention occurs before
   an effective-revision identity exists, retry the generic shard-admission
   operation until it can establish the exact identity or return a typed
   contention failure. Once an identity/digest is authoritative, only the
   existing exact-slot read-only reconciliation may recover a lost response;
   it must never open a replacement binding or select current/latest state.
4. Separate cleanup from primary error classification. Sidecar maintenance
   must not mask a primary SQLite busy/corruption error. Unsafe or malformed
   sidecars still fail closed as `storage_unavailable`; a schema/version,
   migration, or integrity defect remains its precise non-retryable safe class.
   Cleanup must be idempotent and outside the command's semantic success path.

This preserves automatic migration, fail-closed schema behavior, all six public
decision operations, receipt provenance, steering, plan relations, and the
rest of the orchestration feature set. It removes the architectural split
between “pre-command storage work” and the receipt executor instead of adding
another case-specific retry.

### Acceptance criteria for the root fix

| Boundary/invariant | Required source evidence | Required exact-candidate evidence |
| --- | --- | --- |
| Shard/reference admission | A forced lock at compact task resolution, WAL setup, schema readiness, and `BEGIN IMMEDIATE` is handled by the one generic gate for every public command family. | Two independent real stdio processes issue identical `open_clarification`; both succeed with the same binding and no storage error. |
| One contention budget | Tests prove the configured SQLite timeout never exceeds the shared monotonic deadline and no fixed 15-second wait is nested under a shorter budget. | The concurrency case completes within the documented bound and reports no retryable first-call failure. |
| Receipt/replay semantics | Forced busy before identity, after identity, and after commit prove exact replay, one mutation/receipt, and changed-input `command_conflict`; no new binding is created. | Restart/lost-response and concurrent open/record checks pass with zero unexplained replays. |
| Error preservation | Fault injection proves busy remains `storage_busy`, unsafe sidecars remain `storage_unavailable`, and schema/migration corruption remains fail-closed without masking or downgrade. | Candidate tool errors contain only the canonical safe class/action and no traceback or private storage detail. |
| Public feature parity | Clarification, plan-review (approve/request_revision/cancel), steering, assignment/evidence, publication, governance, and closure all use the same admission gate; no tool-specific branch exists. | The complete Phase D suite remains green with zero failures/skips, then the required LLM-driven live-dev run is clean. |

Until these criteria are met, D-ADV-013 and D-CAND-006 remain blocked. Source
receipt tests alone are insufficient because they do not exercise the
pre-receipt compact-reference and schema-admission path used by two real
candidate processes.

## D-CAND-006 remediation re-review — 2026-08-29

This is a second source-only review after the shared-admission remediation. No
production or test files were changed; candidate/live-dev were not run, and no
installed profile or cache was touched. **D-CAND-006 is not source-cleared.**

### Remaining P1 boundary gaps

| ID | Result | Exact source evidence | Required architectural correction |
| --- | --- | --- | --- |
| D-CAND-006a | **P1 remains** | `V12Store.for_record_ref` reads the process-shared `record-locators.db` through `_record_locator_matches` before entering `_with_storage_admission`. The direct SQLite connection has its own literal 0.8-second timeout and busy timeout but no shared deadline, WAL setup, or retry. Its legacy fallback calls `_sync_record_locators` after the canonical shard scan, also outside the admission gate. | Route locator lookup, fallback index repair, and every compact record resolver through the same storage-admission/connection primitive. The locator index is derived, but a first public call must not fail on its contention; canonical rows remain authoritative and repair must not turn a committed mutation into an error. |
| D-CAND-006b | **P1 remains** | `_task_store`'s `_with_storage_admission` restores `_contention_deadline` before the public handler reaches `DecisionAggregate` and `_write`; receipt admission therefore starts a new 0.8-second budget instead of sharing one end-to-end command deadline. `_write_once` retries with `time.sleep(delay)` without clamping the delay to the remaining deadline. | Carry one deadline context from public shard/record admission through domain resolution, receipt `BEGIN IMMEDIATE`, mutation, commit, and reconciliation. Clamp every wait to remaining budget or rely on one centrally configured SQLite wait; no nested fixed sleep or operation-specific retry. |
| D-CAND-006c | **P1 risk remains** | `_connection` retries journal-mode setup by matching the exception string (`"locked"`) rather than the canonical SQLite busy/locked code. A busy-class setup failure with a different message bypasses the generic classifier. Post-commit `_mutation` calls `_sync_record_locators` and can raise after the durable mutation has committed. | Classify transient contention by SQLite error code through one shared primitive; preserve primary typed errors. Make derived locator refresh non-authoritative and non-masking after commit, or include it in an atomic admission/receipt boundary without exposing a committed mutation as a failed first call. |

The remediation does improve the original boundary: `_open_shard_for_task_ref`,
`_open_shard_for_record_ref`, `_read`, `_write`, bootstrap, and known-anchor
verification now invoke `_with_storage_admission`; `_connection` derives its
SQLite timeout from a deadline; and sidecar cleanup no longer replaces a
primary exception raised from the yielded operation. However, those positive
paths do not establish complete coverage because the locator index and some
post-resolution/post-commit work bypass them, and the deadline is scoped to a
store helper rather than the entire public command.

### Migration and fail-closed assessment

Forward migrations remain automatic in `_bootstrap_once` and the known-task/
known-record verification paths. Schema validation and migration failures still
raise typed fail-closed errors, and no replacement binding is created by the
admission retry loop. This is **not sufficient for source clearance**: a
missing/invalid derived locator schema currently uses a separate direct
connection, and post-commit index maintenance can still alter the observed
first-call outcome. The source contract must make canonical mutation success
and derived-index availability explicit and deterministic.

### Evidence status and required tests

The new focused source test
`tests/test_command_receipts.py::test_shared_admission_budget_retries_pre_receipt_busy_and_preserves_primary_error`
proves one synthetic `storage_busy` retry and sidecar primary-error
preservation. It does not prove independent-process convergence through
compact task resolution, locator lookup, schema migration, WAL/pragmas, or
post-commit index refresh. The existing direct cross-process receipt test also
starts after store construction and therefore cannot prove the complete public
path.

Before D-CAND-006 can be cleared, add deterministic source tests that:

- force busy at compact task lookup, locator lookup, locator repair, schema
  readiness/migration, WAL/pragmas, anchor verification, read admission, and
  receipt `BEGIN IMMEDIATE`, asserting one shared deadline and no fixed-delay
  overrun;
- start two independent real stdio processes with the same project and exact
  task reference, prove both identical opens return one binding, and inspect
  one receipt/mutation with no `storage_unavailable`;
- repeat the race for compact record references and assignment/report inputs,
  including a missing/stale locator index, without exposing derived-index
  contention as a committed mutation failure;
- prove changed input returns canonical `command_conflict`, exact replay is
  read-only, and no replacement binding/decision is inserted after busy,
  restart, or lost response;
- inject unsafe sidecar, schema-version, migration, and corruption failures,
  proving the primary typed error is preserved and no malformed state is
  treated as retryable success;
- run the complete exact-candidate matrix only after all of the above source
  checks pass, then keep the required LLM-driven live-dev gate separate.

Until these tests and corrections are complete, D-CAND-006 and D-ADV-013 stay
blocked. The fix must remain generic for all public commands and preserve the
full orchestration feature set.

## D-CAND-006 final bounded source re-review — 2026-08-29

The latest shared-admission implementation was checked against every relevant
source boundary. **D-CAND-006 remains P1-blocked; source clearance is not
granted.** The remediation correctly wraps task-shard lookup, bootstrap,
known-task/known-record verification, reads, writes, WAL setup, and receipt
entry in `_with_storage_admission`, derives the main ledger connection timeout
from its active deadline, and preserves a primary exception when sidecar
cleanup fails during the yielded operation. Forward-only automatic migrations
and fail-closed schema validation remain intact.

The coverage is nevertheless incomplete:

| Gap | Evidence | Impact |
| --- | --- | --- |
| Shared locator lookup bypass | `for_record_ref` calls `_record_locator_matches` before any store admission. That function opens `record-locators.db` directly with a literal 0.8-second timeout/busy timeout and no shared deadline or retry. | Assignment/report/decision/initiative compact references can still fail before canonical shard admission. |
| Locator repair and post-commit refresh bypass | Legacy `for_record_ref` recovery calls `_sync_record_locators` outside the gate; `_mutation` also refreshes the locator index after canonical commit and can surface its failure to the caller. | A derived index race can make a committed public mutation look failed, and the next call may take a different recovery path. |
| Deadline is not end-to-end | `_task_store` restores `_contention_deadline` before domain argument resolution and receipt execution. `_write_once` uses an unclamped `time.sleep(delay)`, and journal setup uses exception-text matching instead of the canonical SQLite busy/locked code. | The public command still has multiple contention policies and can exceed its declared budget or miss retryable setup contention. |

The focused synthetic admission test proves only one artificial busy retry and
sidecar primary-error preservation. Existing direct receipt concurrency tests
start after store construction and do not exercise locator resolution,
post-commit refresh, or two independent stdio handlers. Therefore no source
P0 was found, but the P1 gaps are sufficient to keep both D-CAND-006 and the
exact-candidate gate blocked.

The required correction remains architectural: route *all* canonical and
derived storage access through one process-independent admission primitive,
carry one deadline from public dispatch through receipt/reconciliation, use
SQLite error codes rather than messages, make derived-index refresh
non-authoritative and non-masking after commit, and retain precise fail-closed
schema/filesystem errors. Required forced-boundary, record-reference,
two-process real-stdio, replay/conflict, and corruption tests are listed in
the preceding acceptance matrix. No candidate/live-dev execution was run.

## D-CAND-006 source remediation — 2026-08-29

The public storage boundary now uses one generic process-independent shard
admission policy. `V12Store` applies the same monotonic budget to bootstrap,
WAL negotiation, compact-reference reads, known task/record schema readiness,
and `BEGIN IMMEDIATE` receipt admission. SQLite native timeouts are derived
from the remaining budget rather than retaining a nested fifteen-second wait.
Schema migration/backfill remains automatic and transactionally fail-closed;
normal ready checks are not given tool-specific retry behavior. Sidecar
housekeeping is still fail-closed if primary, but cannot mask an in-flight
busy/corruption/schema failure.

Focused source coverage exercises generic pre-receipt busy retry, sidecar
primary-error preservation, two-process same-receipt convergence, restart/lost
response reconciliation, and changed-input conflict. These are source tests
only; exact candidate stdio and live-dev evidence remain required.

### D-CAND-006 end-to-end admission follow-up

The admission deadline is now context-propagated rather than recreated at
each nested helper. It is inherited by `for_task_ref`, `for_record_ref`, legacy
locator recovery, locator refresh/repair, compact-reference verification,
shard readiness, WAL/pragmas, and receipt writes. SQLite BUSY/LOCKED detection
uses the driver error code rather than message text; every backoff is capped by
the remaining inherited deadline. This preserves a primary busy/corruption/
schema failure through sidecar cleanup. Source tests cover deadline inheritance
and code-based busy classification in addition to the existing process receipt
convergence tests. Exact two-process candidate stdio and live evidence remain
unrun by scope.

### Locator-index and source-stdio closure

The final source decision treats `record-locators.db` as reconstructible derived
state rather than canonical command data. Canonical ledger rows and receipts
commit first; locator refresh is bounded, idempotent reconciliation whose
failure cannot report a false command failure. Compact resolution verifies the
canonical shard and can repair/fall back when the index is stale. Focused
failure injection proves a committed result survives refresh failure and a
restart resolves it. A two-independent-process source `cortex.py` MCP test now
performs initialize → identical `open_clarification` and proves two successes
with one binding. Candidate/live acceptance is still not claimed.

## D-CAND-006 ContextVar remediation re-review — 2026-08-29

The latest propagation change closes the earlier locator/deadline bypasses in
source: `for_task_ref` and `for_record_ref` establish `_ADMISSION_DEADLINE`,
all shard/record helpers inherit it, direct locator lookup and repair derive
their connection timeouts from it, and the selected store carries the same
deadline into later domain/reference/receipt work. `_connection`, journal
setup, and `_write_once` use SQLite error codes and cap waits to remaining
time. Sidecar cleanup still preserves a primary yielded-operation exception.

No production P0/P1 defect was found in the current source path. Automatic
forward migrations, transaction rollback, and fail-closed schema validation
remain intact. The current focused tests prove ContextVar inheritance,
code-based busy classification, synthetic pre-receipt retry, primary-error
preservation, and direct receipt replay/concurrency.

### Source versus exact-candidate evidence adjudication

The existing Phase D qualification test starts two independent candidate
stdio processes and concurrently calls the public `open_clarification` and
`record_clarification` handlers. Once rerun against the current content-
addressed candidate and green, that is sufficient **black-box candidate
evidence** for the process-independent convergence invariant: it observes the
actual MCP wire, two OS processes, shared isolated storage, one binding, one
receipt/decision, and exact replay. A separate source stdio test must not
replace that candidate gate.

It is not, however, sufficient as *source-clearance evidence* under the
repository's explicit separation of source, candidate, and live gates. The
current source tests exercise `serve_stdio` in-process and exercise the store
executor directly, but no source-mode test launches two independent stdio
`cortex.py` processes through the MCP handler. Add one bounded source stdio
regression (same project, two processes, exact task ref, forced/observed first
open contention, no tool error, one binding/receipt, changed-input conflict),
then retain the exact-candidate test as the packaging black-box gate. This is
an evidence P1 gap, not permission to lower the one-binding/one-receipt
invariant or to accept a candidate failure.

### Clearance decision and next gate

D-CAND-006 is **source-supported but not source-cleared** until that bounded
source MCP integration test is present and passes. Candidate qualification
remains separately blocked until the exact current candidate produces zero
failures and zero skips; only then may the required LLM-driven live-dev gate
run. No candidate/live-dev execution was performed in this review.

## D-CAND-006 ContextVar final scope adjudication — 2026-08-29

The ContextVar propagation is correct for the task/record resolution and
receipt paths: compact task/record entry establishes one deadline, nested
locator lookup/repair, shard readiness, WAL/pragmas, reads, writes, and
receipt admission inherit it, all retry sleeps are capped, and SQLite busy
classification is numeric rather than message-based. Sidecar cleanup preserves
primary errors from the yielded operation; forward migrations remain automatic
and fail-closed.

One public legacy mutation boundary still needs explicit scope review:
`_mutation` calls `_write`, then performs `_sync_record_locators` after the
receipt/legacy mutation helper has returned and its admission context has been
reset. The refresh is derived and canonical rows remain authoritative, but a
locator-refresh error can still be observed after a committed mutation under a
new budget. The complete public-command deadline contract must either encompass
that refresh or make it non-authoritative and non-failing to the caller.

The two-process exact-candidate test is sufficient black-box candidate evidence
once green; it is not source-mode evidence. Because current source tests are
in-process/direct-store only, add the bounded source stdio concurrency test
before source clearance. This is an evidence P1 gate, not a relaxation of the
one-binding/one-receipt invariant. No candidate/live-dev execution was run.

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

## D-CAND-006 read-only source re-review after host restart — 2026-08-29

The bounded source suite was rerun with `PYTHONDONTWRITEBYTECODE=1` and
`PYTHONPATH=plugins/cortex/scripts`:

```text
29 passed, 16 subtests passed
```

This is source evidence only. Candidate qualification, live-dev, and the
installed profile were not run or changed.

The source is **not cleared** for D-CAND-006. The current implementation
contains the following still-reproducible boundary defects:

| Boundary | Evidence | Consequence |
| --- | --- | --- |
| Fallback record resolution | `V12Store._for_record_ref_once` calls `_sync_record_locators()` synchronously after a canonical record has already been found by the shard scan. A forced repair failure returns `storage_unavailable` instead of the verified canonical record. | A reconstructible accelerator can still block a canonical assignment/report/decision/initiative reference. |
| Indexed locator read | `_record_locator_matches` raises `storage_unavailable` for a missing/invalid locator schema or a sidecar read failure instead of treating the sidecar as unavailable and falling back to canonical shard verification. | Sidecar corruption/unavailability can be misreported as a canonical storage failure. |
| Bootstrap sidecar rebuild | `_bootstrap_once` performs `_sync_record_locators()` after committing canonical schema/data. A forced sidecar failure aborts `V12Store` construction even though the canonical database exists. | Canonical storage is inaccessible solely because a derived index could not be rebuilt. |
| Source MCP acceptance scope | `tests/test_command_receipts.py::test_two_process_stdio_identical_open_converges_through_full_admission` starts two independent `cortex.py` processes and checks initialization, compact task resolution, shard readiness, successful identical opens, and one binding row. It does not assert the `command_receipts` row, changed-input `command_conflict`, or locator fallback/repair. | The test cannot grant the claimed one-binding/one-receipt plus conflict and reconstruction guarantee. |

The positive properties remain supported by source tests: a post-commit
refresh failure is swallowed by `_refresh_record_locators_after_commit`, the
admission deadline is propagated through the covered nested paths, SQLite
busy classification uses numeric error codes, waits are capped, and primary
operation errors are preserved across sidecar cleanup. Those properties do
not override the three sidecar bypasses above.

Required next evidence is a generic sidecar policy test (bootstrap, indexed
read, fallback repair, and post-commit refresh), plus one source-mode
two-process MCP test that asserts exactly one canonical binding and receipt,
changed-input conflict, and safe fallback after locator corruption. The exact
candidate matrix and LLM-driven live-dev gate remain separate and blocked.

## D-CAND-006 independent post-remediation race review — 2026-08-29

The current locator-authority injections pass: canonical fallback survives a
repair failure, malformed locator bytes fall back and are rebuilt for the next
resolver, bootstrap remains usable when derived rebuild is unavailable, and a
canonical schema failure is not downgraded. The strengthened source MCP test
also now asserts identical binding, one binding row, one `open_clarification`
receipt, one record mutation/receipt, and changed-input `command_conflict`.

The source gate nevertheless remains **P1-blocked**. A repeated independent
process race reproduced `storage_unavailable` during otherwise identical
`for_task_ref` admission. Instrumentation identified the exact failing path:
`_materialize_sidecars` validates `cortex.db-wal`, SQLite concurrently unlinks
it while the last connection closes, and the subsequent `os.chmod()` raises
`FileNotFoundError`, which is projected as `storage_unavailable`. This is a
canonical shard-admission failure, not a locator-sidecar failure, and the
shared retry wrapper does not recover it because the failure is raised after
connection close in sidecar housekeeping.

Commands and observed results:

```text
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=plugins/cortex/scripts \
python3 -B -m pytest -q tests/test_command_receipts.py \
tests/test_phase_d_candidate_root_cause.py tests/test_public_mcp_first_call_conformance.py \
tests/test_decision_aggregate.py tests/test_domain_kernel_receipts.py
33 passed, 16 subtests passed

Repeated two-process source admission race: one process intermittently
returned storage_unavailable while the other returned a successful identical
binding. The exact race was reproduced in an isolated temporary project and
the failing `os.chmod(cortex.db-wal)` path was captured.
```

The required root correction is to make SQLite WAL/SHM housekeeping race-safe
and part of the same bounded admission policy: a disappearing SQLite-managed
sidecar must be re-observed/recreated within the inherited deadline, while
unsafe path types and canonical database/schema failures remain fail-closed.
Do not turn this into an operation-specific retry or weaken the one-binding/
one-receipt invariant. Candidate/live evidence remains unrun.

## D-CAND-006 locator-authority source closure — 2026-08-29

The remaining sidecar boundary is now corrected at the common storage layer.
`record-locators.db` is uniformly a reconstructible, non-authoritative
accelerator: malformed, unreadable, schema-incompatible, or busy locator reads
fall back to bounded canonical shard verification; a repair failure after an
exact canonical result cannot overturn that result; and a malformed derived
image is atomically rebuilt from verified current-shard rows. Bootstrap uses
the same best-effort repair only after canonical migration and validation, so
a sidecar failure cannot deny access to a canonically ready database.

Canonical root safety, canonical SQLite/schema/migration errors, and canonical
record verification remain fail-closed. One inherited admission deadline,
numeric SQLite busy detection, bounded waits, and primary-error preservation
are retained without operation-specific recovery or fixed sleep.

`tests/test_command_receipts.py` now covers deterministic fallback repair
failure, malformed-sidecar fallback with restart repair, and bootstrap repair
failure with canonical reuse. Its real two-process source `cortex.py` MCP
regression proves two identical opens produce one binding and one matching open
receipt; a later changed response on that binding returns `command_conflict`
with no duplicate decision or record receipt. This is source-only evidence.
Exact candidate qualification and the LLM-driven live-dev gate remain unrun.

## Final exact-candidate evidence — 2026-08-29 (supersedes prior gate status)

The packaging root was corrected by making `plugins/cortex/runtime-payload.json`
the canonical runtime closure. A fresh isolated sync staged candidate
`1.12.1+codex.sha256.eb691a9a49377dcc` with digest
`eb691a9a49377dcc24640d415c5fa38d8e94b7cb33c104277443c5c3004c453f`;
marketplace/release validation passed for 94 files. The actual candidate
stdio server reported `parityVerified=true` after checkout `PYTHONPATH` and
`CORTEX_SOURCE_MODE` were removed and isolated `HOME`/`CODEX_HOME` were set.

The complete candidate matrix passed: `11 passed, 0 failures, 0 skips, 0
errors`. The exit-aware 80-pair stress also passed. Every pair used two
independent candidate children and converged on one binding/open receipt, one
record mutation/receipt, exact replay, and a changed-input `command_conflict`.
No duplicate mutation, nonzero exit, forced termination, hidden EOF, SIGBUS,
stderr, or observed Python-side WAL/SHM sidecar remained. This promotes the
Decision vertical slice from candidate-gap to candidate-verified. The only
remaining gate for this slice is the focused LLM-driven live-dev run; no live
or stable-profile execution is claimed here.

## D-CAND-006 final WAL/SHM stress adjudication — 2026-08-29

The deterministic locator and WAL-disappearance injections pass, and the
source MCP scenario proves the intended binding/receipt/conflict invariants on
successful runs. Source clearance is nevertheless **withheld (P1)**. A
bounded repetition of the four-round independent-process stress (20 test
runs, 80 process races) failed: one source MCP child closed stdout before its
JSON-RPC response while the peer returned a replayed success. A separate
80-race harness also observed a child exit with `-7` (SIGBUS). This is a real
hidden-response/process-crash failure in the SQLite WAL/SHM admission path,
not a qualification assertion issue. Candidate/live gates remain blocked
until the race is root-corrected and the full bounded stress completes with no
errors, crashes, hangs, split bindings, or leaked children.

## D-CAND-006 final independent source adjudication — 2026-08-29

This section supersedes earlier optimistic wording. The locator-authority
injections pass, but D-CAND-006 is **not source cleared**. An independent
repeated two-process source admission run still reproduces
`storage_unavailable` in `_materialize_sidecars`: SQLite can unlink
`cortex.db-wal` after `_regular()` succeeds and before `os.chmod()` executes.
The resulting `FileNotFoundError` is projected as a storage failure after the
canonical connection closes, outside the shared busy retry/reconciliation
boundary. The generic fix must make SQLite-managed WAL/SHM housekeeping
race-safe and bounded while preserving fail-closed unsafe-path and canonical
schema checks. Candidate and live-dev remain unrun.

## D-CAND-006 WAL/SHM admission-race source closure — 2026-08-29

The root cause was broader than a disappearing pathname. Cortex had been
creating SQLite-owned WAL/SHM placeholders after connection close, while a
second process could be entering `PRAGMA journal_mode=WAL`. That external
materialization can split or stall SQLite's own WAL admission; it is not a
valid repair mechanism. Cortex now never creates WAL/SHM. The shared safety
path only descriptor-validates and owner-normalizes an extant regular sidecar;
if a verified SQLite-owned sidecar disappears before or after `fchmod`, it is
re-observed under the inherited deadline and benignly left absent.

A descriptor-validated private per-shard admission lock serializes only
connect/WAL-mode/safety setup. It uses the already inherited monotonic budget,
capped contention waits, and no operation-specific retry; normal transactions
remain SQLite-owned. Symlinks, wrong types, path escape, persistent permission
faults, canonical corruption/schema failures, and non-transient I/O errors
remain fail-closed. Numeric `SQLITE_BUSY`, `SQLITE_LOCKED`, and
`SQLITE_PROTOCOL` retain their bounded classification.

Focused source evidence is green: deterministic deletion between sidecar
validation and descriptor chmod succeeds without a storage error and the
canonical database remains readable; the real two-process `cortex.py` MCP
test proves same binding, one canonical binding/receipt, one decision/receipt,
and changed-input `command_conflict`; the repeated four-round source-MCP
stress converges with no duplicate mutation, hidden tool error, timeout, or
leaked test child. This clears the **source** P1 blocker only. Exact-candidate
and LLM-driven live-dev evidence remain unrun.

## SQLite sidecar final source update — 2026-08-29

The final source policy is stricter than the earlier descriptor-normalization
closure: Cortex never mutates a live WAL/SHM, including during admission.
It validates an extant sidecar only under a PID-aware, reentrant per-shard
connection-lifetime lease. The lease now remains held through SQLite close,
because 80-pair source stress showed startup-only release could still produce
two returned bindings despite one canonical binding/receipt. The deliberate
tradeoff is generic per-shard Cortex connection serialization. Exit-code-aware
80-pair source stress is green; candidate/live remain unrun.

## D-CAND-006 final independent WAL/SHM stress adjudication — 2026-08-29

The deterministic locator and WAL-disappearance injections pass, but the
source gate remains **P1-blocked**. Repeating the four-round independent
source-MCP stress 20 times (80 process races) failed: one source child closed
stdout before its JSON-RPC response while the peer returned a replayed success.
An independent 80-race harness also observed a child exit `-7` (SIGBUS).
This is a real hidden-response/process-crash failure in SQLite WAL/SHM
admission, not a qualification assertion issue. Candidate/live gates remain
blocked until the race is root-corrected and bounded stress completes without
errors, crashes, hangs, split bindings, or leaked children.
## D-CAND-006 final connection-lifetime lease review — 2026-08-29

The current source was re-reviewed read-only after the connection-lifetime
lease remediation.  The active `V12Store._connection` path acquires a
descriptor-validated, PID-aware, reentrant lease before `sqlite3.connect`,
holds it through WAL/pragmas, the caller transaction, and connection close,
and releases it on exceptions.  The lease is keyed by the shard root, so
unrelated shards remain independent.  Numeric `SQLITE_BUSY`, `SQLITE_LOCKED`,
and `SQLITE_PROTOCOL` handling and capped sleeps inherit one 0.8-second
admission deadline.  Canonical database protection is separate from the
non-mutating active WAL/SHM validation; symlink and directory sidecars fail
closed.  The focused suite passed (`38 passed, 96 subtests`), including the
80-subtest simultaneous two-process source `cortex.py` MCP stress and the
record/conflict scenario.

Source clearance is nevertheless **withheld (P1)**.  The source tree still
contains `_secure_sqlite_sidecar`, whose implementation calls
`_secure_regular_file` and therefore `os.fchmod` on a WAL/SHM path.  Current
runtime call sites use `_protect_admitted_sidecars` and do not invoke this
helper, but the source-level invariant is “Cortex never mutates live WAL/SHM
anywhere”; a dormant callable violating that invariant is not acceptable.
The helper must be removed or made validation-only, and its deletion-race
test must be replaced with a no-mutation fail-closed test.  No candidate or
live-dev result is promoted from this review.

## Latent-path source closure — 2026-08-29

The helper has been removed and source AST conformance now rejects every
production WAL/SHM path paired with filesystem mutation. This covers dormant
as well as reachable code; canonical database protection remains separate.
## D-CAND-006 final post-removal clearance check — 2026-08-29

The previously identified `_secure_sqlite_sidecar` helper is now absent.  A
source inventory of functions mentioning `cortex.db-wal`/`cortex.db-shm`
shows only `_regular` validation in the active store path; canonical database
mode protection remains separate.  The source MCP stress remains green:
80 simultaneous pairs, clean exit-aware completion, and the focused source
checks pass.

Clearance remains **withheld (P1)** because the new AST conformance check is
not a sound source-level guard.  It searches for literal `-wal`/`-shm` text
inside each function and then scans for lexical spellings such as `os.open`,
`os.fchmod`, `os.replace`, or `.unlink(`.  A computed suffix, aliased call,
or helper indirection can bypass that check.  The implementation currently
passes the inventory, but the qualification guard must be strengthened to
assert sidecar-path data flow/call targets (or use an explicit forbidden
sidecar filesystem API boundary) before source clearance.  Candidate and
live-dev remain unrun.

The package-wide reviewed mutation-capability policy closes alias, computed,
dynamic, and helper-indirection bypasses for the validation-only sidecar rule.

## Mutation-boundary clearance — 2026-08-29

The former lexical P1 finding is superseded. The runtime package and its
`cortex.py` launcher are parsed as one mutation boundary: a direct,
aliased, pathlib, or dynamic filesystem mutator is rejected unless it belongs
to a centrally named capability with one exact purpose. The registry grants no
live SQLite sidecar mutation capability. Canonical database protection and
offline validated backup retention remain explicit, separate purposes.

A `sitecustomize` observer now runs inside each exec'd source MCP process in
the 80-pair stress. It fails the stress on any Python-level `-wal`/`-shm`
mutation while intentionally leaving SQLite's C-owned operations unwrapped.
This closes the source-level bypass class; exact candidate and live-dev remain
separate, unrun gates.

## Recursive proof-boundary closure — 2026-08-29

The mutation proof now descends through every runtime Python module and checks
the launcher. It fails closed for assignment/import aliases, `pathlib` module
aliases, nested-module calls, `getattr` and subscript lookup, helper aliases,
and global/default/closure callable storage. Descriptor-level source-process
observation covers `write`, `pwrite`, `ftruncate`, `truncate`, and pathname
mutators after resolving the opened descriptor back to its path.
## D-CAND-006 final filesystem-boundary review — 2026-08-29

The removed sidecar helper remains absent.  The active store path validates
live WAL/SHM names only, and the source MCP stress still passes 80
simultaneous pairs with clean child exits.  `sitecustomize` is demonstrably
loaded inside the exec'd source MCP child: the observer probe records a
sidecar-path chmod from a separate child process.

Source clearance is still **withheld (P1)**.  `filesystem_policy.py` rejects
the currently tested direct/import-alias/pathlib/dynamic/helper samples, but
it does not reject assignment aliases (`erase = os.unlink`), aliased pathlib
modules (`import pathlib as p; p.Path(...).unlink()`), or helper indirection
through those forms.  The scanner also uses non-recursive `glob("*.py")`, and
the observer omits low-level mutation primitives such as `os.write` and
`os.ftruncate`.  Therefore the package-wide
no-live-sidecar-mutation claim is not yet non-bypassable.  Candidate and
live-dev remain unrun.
## D-CAND-006 final filesystem-policy adversarial review — 2026-08-29

The strengthened policy now recursively scans the package, rejects assignment
aliases, aliased pathlib constructors, nested modules, defaults/closures, and
ordinary `getattr` dynamic lookup.  The exec-child observer covers path and
FD mutation calls including `write`, `pwrite`, and `ftruncate`; the 80-pair
source-MCP stress remains green.

Source clearance remains **withheld (P1)**.  Read-only adversarial fixtures
still bypass the policy through `os.__dict__["unlink"](...)` (the checker
continues before its subscript branch when the call target itself is a
subscript) and through helper-returned/callback-stored mutators such as
`return os.unlink`, `functools.partial(os.unlink)`, or a later callback
invocation.  These fall within the requested subscript and helper-indirect
scope and must be rejected before clearance.  Candidate/live-dev remain
unrun.

## Final callable-escape closure — 2026-08-29

Call-target inspection now checks dynamic subscript chains before the normal
attribute path, so `os.__dict__['unlink'](...)` and equivalent pathlib/path/
shutil chains cannot bypass the policy. The policy also rejects returning,
yielding, container/attribute storing, wrapping with `partial`, passing as a
callback, or capturing/exporting a mutator callable through nested helpers.

## Returned-constructor flow closure — 2026-08-29

The static proof now summarizes helper returns carrying `pathlib.Path` and
filesystem module identity. A helper-returned constructor, an assignment of
that constructor, and chained one/two-hop helper returns are resolved before
the indirect constructor/module call is checked for mutation.
## D-CAND-006 final callable-flow review — 2026-08-29

The latest checker closes the prior direct practical bypasses: nested
subscripts, return/yield exports, partials, callbacks, containers, object
attributes, decorators, closures, nested helpers, and FD-dup/write-family
observer coverage are now represented by negative fixtures or wrappers.

One P1 remains.  A helper-returned pathlib constructor can still be used as
`helper()(path).unlink()` without a conformance failure because the checker
does not propagate callable identity for returned `pathlib.Path` constructors
through a call before the final method.  This is within the requested
helper-indirect runtime scope.  Source clearance remains withheld; candidate
and live-dev remain unrun.
## D-CAND-006 final declared-scope clearance — 2026-08-29

The final narrow audit closes the last returned-constructor/module-flow gap.
Direct invocation, assignment, one-hop and two-hop helper returns for
`pathlib.Path`, `pathlib`, `os`, and `shutil` all fail conformance when they
lead to a mutator.  The prior subscript, callback, container, object,
default, decorator, closure, nested-helper, and FD-dup/write-family cases
remain rejected or observed.  The focused guard/domain run passed (`3
passed, 106 subtests`), including the 80-pair source-MCP stress.

Within the declared Python/runtime scope, D-CAND-006 is **source-cleared**.
This does not claim exact-candidate or live-dev evidence; those gates remain
separate and unrun.

## Candidate payload closure root cause — 2026-08-29

The first exact-candidate finalization exposed a separate P1 delivery gap:
`filesystem_policy.py` was a real production runtime module in the checkout,
but the candidate payload closure was maintained by a hand-edited seed list
that did not contain it. Import closure could not discover a module that was
used by source qualification without a production import edge. Consequently,
source and candidate evidence did not describe the same executable boundary.

The root correction is one manifest/closure boundary, not an allowlist patch.
`plugins/cortex/runtime-payload.json` declares the complete launcher and
`cortex_runtime` Python set. Candidate construction derives the actual module
set, compares it with the declaration, and fails closed on missing, extra,
duplicate, unsafe, symlinked, or non-regular entries. The marketplace gate
consumes the same declaration. The manifest is staged as part of the plugin
payload and therefore participates in the deterministic content-addressed
identity. Focused packaging tests prove staging/importability of
`filesystem_policy.py`, rejection of an unlisted module, and identical build
IDs for identical source trees. This closes the packaging boundary only;
Decision exact-candidate and live-dev acceptance remain open until their own
gates pass.

The implementation is now a shared closure boundary in
`scripts/cortex_payload_manifest.py`, used by both candidate construction and
marketplace validation. It recursively discovers nested runtime packages,
requires package initializers, validates all entry types, and compares exact
directory topology as well as files. Repository, candidate-version, and
plugin roots are checked with `lstat` before path normalization. This removes
the prior class of drift where the builder and validator interpreted a
different runtime payload or silently accepted an empty undeclared directory.

The three source `storage_unavailable` observations were reproduced as
inherited-state test contamination, not a candidate storage defect: the
marketplace tests did not isolate the `CODEX_HOME` used by compact-reference
resolution. The test harness now provisions a temporary state root per test
and restores the process environment. A focused rerun has zero
`storage_unavailable` failures; unrelated stale assertions remain for the
closed decision/publication projection shape and are intentionally outside
this packaging correction.

## Trusted ancestry P1 closure — 2026-08-29

The remaining delivery root was path ancestry, not runtime semantics. A final
plugin directory check could still accept a version path reached through a
symlinked cache parent, and marketplace validation did not reject empty
undeclared plugin directories. The shared lstat chain and exact-topology
helpers now close both paths. Sync validates cache and version roots before
staging/reuse, while candidate and installed-plugin checks validate every
managed ancestor. Missing directories are created only after safe-ancestor
validation and are rechecked after creation. The stale source test state root
is independently isolated per marketplace test. Focused evidence is green;
the full Decision candidate/live gates remain required and are not claimed.

## Current root-cause adjudication — 2026-08-29

The exact content-addressed candidate gate is now **verified**. The fresh
isolated candidate `1.12.1+codex.sha256.eb691a9a49377dcc` passed marketplace
and release validation (`files=94`), reported `parityVerified=true`, and ran
with isolated state after removing checkout `PYTHONPATH` and
`CORTEX_SOURCE_MODE`. The full candidate Decision suite passed 11/11 with no
failures, skips, or errors. Its 80 simultaneous-pair stdio stress proved one
binding/open receipt and one record mutation/receipt per pair, exact replay,
changed-input `command_conflict`, clean child exits/stderr, no SIGBUS/EOF, and
no observed Python-side WAL/SHM sidecars. This closes the candidate-side
packaging, provenance, admission, receipt, and decision-vertical evidence
gaps recorded earlier. The focused LLM-driven live-dev gate remains next and
has not been run.
