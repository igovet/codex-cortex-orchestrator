# Decision vertical-slice capability parity matrix

Status: current development migration gate for the Cortex `1.12.1`
architecture. The selected design is the narrow 15-tool decision boundary.
The latest live evidence remains **failed/unverified**: the content-addressed
candidate passed provenance checks, but mid-work steering produced a
`capability_stale` result in the pre-fix build. Final live qualification is
pending; no capability row is promoted by that run.

The current architecture is a narrow 15-tool public catalogue. Clarification,
plan review, and steering each have a matching open/record operation pair;
each record operation consumes only its matching server-owned binding. Steering
uses flat collections at the host admission boundary, and every assignment
receives an immutable capability snapshot. A steering change therefore affects
subsequent assignments without silently changing a worker already in progress.

The catalogue is `open_task`, `read_task`, `open_clarification`,
`record_clarification`, `open_plan_review`, `record_plan_review`,
`open_steering`, `record_steering`, `open_assignment`,
`consume_assignment_evidence`, `publish_plan`, `publish_result`,
`publish_documentation`, `assess_governance`, and `close_task`. This list is
architecture vocabulary only; callable request details remain defined by the
advertised tool schemas.

Plan review must use the verified server-rendered approval view and Markdown
link returned by `open_plan_review`. The coordinator delivers that result
without reconstruction and records the answer through the same binding. Lost
responses use read-only reconciliation, never a replacement binding.

The candidate keeps server version `1.12.1` while its cache identity is
content-addressed. Live smoke remains transport-only and LLM-driven. Historical
observation leases remain readable after revocation, while claim authority is
removed and a fresh generation is required for a new session.

The older family-API diagnosis below is retained as historical analysis. It is
not the current disposition and must not be used to reintroduce separate public
record operations.

The blocking source findings in the Phase D adversarial review are remediated
at the family adapter, aggregate, storage, schema, and MCP-wire boundaries as
recorded in the [Phase D decision remediation matrix](phase-d-remediation.md).
That source-level status does not replace the candidate or live evidence
required by the rows below.

The independent hold/event-journal review adds a current source gate over the
clarification continuation boundary and hidden-worker observations. It is
recorded in [phase-d-hold-event-review.md](phase-d-hold-event-review.md).
Source clearance remains withheld: remaining P1 findings cover incomplete malformed
`tools/call` observation, ancestor-chain journal safety, wire/event outcome
alignment, and a red focused source test. The renderer subject-projection and
public dispatch-digest findings are source-remediated with focused adversarial
coverage; the other findings still block source clearance. The family rows below therefore stay
source-tested/evidence-blocked and no candidate or live status is promoted.

This matrix is the decision-specific preservation contract. It is deliberately
separate from the general [orchestration feature parity inventory](orchestration-feature-parity.md):
the inventory says what Cortex must continue to do, while this document says
where each decision behavior is owned and what evidence is required before the
current narrow public boundary can be accepted.

The matrix records the state of the checkout when it is updated. A source
implementation or a direct store test is not candidate qualification, and
candidate qualification is not live acceptance. No row may be promoted to
`verified` without the evidence named in the final column. The matrix contains
no private logs, credentials, opaque runtime identifiers, or model-facing
argument recipes.

The original five candidate failures are grouped by shared architectural invariants in
the [Phase D candidate root-cause map](phase-d-candidate-root-cause.md). The
rows below are preservation capabilities, not case-specific error patches.

## Candidate gate update — 2026-08-29

The package-hygiene blocker was cleared by removing only three empty generated
`__pycache__` directories under the plugin/tests scopes. The exact candidate
stdio suite then ran with checkout imports and source mode removed and reported
`parityVerified=true`. The command produced **5 passed, 5 failed, 0 skipped,
0 collection errors**. Consequently every row below remains unverified at the
candidate gate; no partial pass is promoted. The failures are concentrated in
semantic conflict/stale error translation, concurrent open busy handling, plan
approval-view delivery, and public supersession evidence. See
[Phase D qualification](phase-d-qualification.md) and the
[adversarial review](phase-d-adversarial-review.md) for the sanitized evidence
and required root remediations. Live acceptance remains unrun.

## Superseded family boundary and ownership (historical)

The following family matrix records the earlier six-operation proposal for
forensic comparison. It is superseded by the narrow 15-tool boundary above;
its operation names and status claims are not current qualification criteria.

The decision boundary consists of three typed families, each with an open and a
matching record operation:

| Family | Public operations | Domain owner | Capability produced and consumed |
| --- | --- | --- | --- |
| Clarification | `open_clarification` → `record_clarification` | `DecisionAggregate` | A clarification binding is consumed only by its matching record operation. |
| Plan review | `open_plan_review` → `record_plan_review` | `DecisionAggregate` | A plan-review binding is consumed only by its matching record operation. |
| Steering | `open_steering` → `record_steering` | `DecisionAggregate` | A steering binding is consumed only by its matching record operation. |

The public composition root is
`plugins/cortex/scripts/cortex.py`; the six adapter functions are in
`plugins/cortex/scripts/cortex_runtime/domain_api.py`; public contract
construction is in `plugins/cortex/scripts/cortex_runtime/public_contracts.py`;
registry metadata is in
`plugins/cortex/scripts/cortex_runtime/semantic_registry.py`; and the
transactional implementation is in
`plugins/cortex/scripts/cortex_runtime/domain_kernel.py` and
`plugins/cortex/scripts/cortex_runtime/v12_store.py`.

The coordinator remains responsible for wording questions, choosing when to
ask, selecting workers, scheduling/DAG adaptation, interpreting the user's
answer, deciding governance depth, selecting rework, and synthesizing the
final response. The backend remains responsible for identity, scope,
immutable relation checks, legal transitions, atomic persistence, receipts,
and safe errors. The split does not remove any orchestration capability.

## Capability matrix

Status vocabulary:

- `implemented (source)` means the production location exists and is directly
  identifiable, but no candidate or live claim is made.
- `source-tested` means a named checkout-level test exercises the behavior;
  it is still not candidate or live evidence.
- `candidate gap` means the required exact-candidate stdio evidence is absent,
  stale, or still exercises the retired public boundary.
- `live unverified` means the required real LLM-driven tmux observation has not
  been run or has not produced clean evidence.
- `blocked` means a concrete implementation or evidence defect prevents the
  row from being accepted.

| Capability that must be preserved | Public API path | Production implementation location | Current evidence | Required candidate evidence | Live acceptance requirement | Status |
| --- | --- | --- | --- | --- | --- | --- |
| Clarification question and answer recording | `open_clarification` → `record_clarification` | `domain_api.py`; `DecisionAggregate.open_clarification` / `.record_clarification`; store binding and decision rows | `tests/test_decision_aggregate.py::DecisionAggregateTests.test_binding_is_server_owned_and_record_is_atomic_replay` covers the shared aggregate path | `tests/test_phase_d_decision_qualification.py::test_clarification_is_exactly_once_localized_and_replay_safe` exercises one exact-candidate stdio open/record pair from the advertised contracts | LLM asks one real product clarification, user answer is delivered once, and the matching record result is observed | source-tested; candidate gap; live unverified |
| Arbitrary localized prompt and byte-exact original response | Historical unified record operation | `domain_kernel.py` passes the bound prompt context to the internal decision persistence layer | Existing direct tests use English only and do not prove arbitrary Unicode/localized bytes | Candidate test must use a non-English prompt and response with Unicode edge cases, then compare the persisted/projected original response byte-for-byte | Live pane and structured worker/coordinator evidence must show the answer was recorded without translation or summarization | historical; superseded |
| Server-owned stable pending binding | Any `open_*` operation | `domain_kernel.py::DecisionAggregate.open`; `v12_store.py::issue_clarification_binding`; logical-slot receipt table | `tests/test_decision_aggregate.py` and `tests/test_v15_clarification_bindings.py` cover repeated opens through direct aggregate/store calls | Same intent opened twice in one candidate process and after restart must return the same scalar pending binding or consumed projection | LLM must reuse the exact returned binding; no reconstructed or replacement binding may appear in the observed calls | source-tested; candidate gap; live unverified |
| Exact consume, replay, changed-response conflict, stale revision, and cross-project isolation | Matching `record_*` only | `domain_kernel.py::DecisionAggregate.record` plus store binding/revision/project checks and command receipts | Direct store tests cover replay, changed response, stale revision, and concurrent consume; aggregate test covers replay/reconcile | Candidate stdio must prove one commit, exact replay of the same receipt, changed-input conflict with zero second mutation, stale failure, and cross-project failure | Any tool error or unexplained replay in the live pane/event stream fails; recovery may only reconcile the original binding | source-tested; candidate gap; live unverified |
| Plan approval against one immutable plan/view relation | `open_plan_review` → `record_plan_review` with `approve` | `domain_api.py`; `DecisionAggregate.open_plan_review` / `.record_plan_review`; v17 binding relation columns and approval-handle checks in `v12_store.py` | `test_plan_review_binding_survives_newer_view_concurrency_and_restart` persists a relation, creates a competing newer view/handle, records concurrently, and replays after a new aggregate. The exact-candidate test is defined in `tests/test_phase_d_decision_qualification.py::test_plan_review_outcomes_bind_one_immutable_plan`. | Candidate must obtain the plan publication from server state, record approval against the returned review binding, and assert exact digest/view/source sequence in the durable receipt, including competing views | Coordinator visibly approves the rendered plan once; no manually reconstructed plan relation or duplicate approval is accepted | source-tested; candidate gap; live unverified |
| Plan revision request and cancellation remain distinct durable outcomes | `record_plan_review` with `request_revision` or `cancel` | `DecisionAggregate.record_plan_review`; persisted v17 plan-review relation and decision projections | `test_receipts_use_public_semantic_family_names` drives all three outcomes; the aggregate record path supplies and validates the stored relation for each outcome. Parametrized exact-candidate coverage remains defined in `tests/test_phase_d_decision_qualification.py::test_plan_review_outcomes_bind_one_immutable_plan`. | Candidate must run each outcome against the same immutable plan/view and assert the outcome, relation, receipt, projection, and no accidental approval | LLM must answer the rendered plan question explicitly; observed lifecycle must take the corresponding coordinator-owned revision/cancel path | source-tested; candidate gap; live unverified |
| Same-task steering, effective-contract revision, and supersession | `open_steering` → `record_steering` | `domain_api.py`; centralized `DecisionAggregate` family operation map; persisted steering delta/effective contract in `v12_store.py` | `tests/test_v15_clarification_bindings.py` directly exercises a steering-induced stale binding and `test_receipts_use_public_semantic_family_names` checks the exact public receipt name. The exact-candidate public supersession path is defined in `tests/test_phase_d_decision_qualification.py::test_steering_creates_one_effective_revision_and_explicit_supersession`. | Candidate must record one non-empty steering delta, create exactly one next effective revision, preserve the prior revision, verify explicit supersession, and assert the public command name in the receipt; replay must not create another revision | LLM observes and answers the steering question; coordinator retains control of task adaptation and worker assignment | source-tested; candidate gap; live unverified |
| Lost-response reconciliation without a new binding or mutation | Any record operation followed by read-only reconciliation | `DecisionAggregate.reconcile`; command receipt lookup in `v12_store.py`; coordinator recovery policy | `test_plan_review_binding_survives_newer_view_concurrency_and_restart` proves exact plan-review receipt replay through a new aggregate after concurrent consumption; `tests/test_domain_kernel_receipts.py` proves generic receipt lookup. | Candidate process must commit, discard the response, restart, and reconcile using the original binding; it must prove no replacement binding, decision, or receipt | A transport ambiguity must lead only to read-only reconciliation in the observed session; opening a new logical decision is a failure | source-tested; candidate gap; live unverified |
| Context-compaction and process-restart recovery | Re-open/read projection plus original typed binding | Durable task/binding/receipt state in `v12_store.py`; recovery policy in bundled context/orchestrator skills; aggregate reconciliation | `test_plan_review_binding_survives_newer_view_concurrency_and_restart` reopens the store in a new aggregate and receives the original plan-review receipt; `tests/test_phase_d_decision_qualification.py::test_restart_and_lost_response_reconcile_without_new_binding` defines the full public path. | Exact-candidate test must restart the real MCP process and recover task, family, binding, decision, revision, and receipt state without inferred identifiers | Live coordinator must resume from observed durable state and must not ask the user to reconstruct an opaque handle | source-tested; candidate gap; live unverified |
| Governance remains advisory and nonblocking | Decision operations plus `assess_governance` | Coordinator governance policy; registry ownership metadata; `domain_api.py::assess_governance` | General parity and direct governance tests identify advisory ownership; no decision-specific candidate proof | Candidate must show a governance failure/assessment does not authorize unsafe work and does not prevent an honest user-facing result | LLM retains governance judgment; backend cannot silently convert advisory governance into an autonomous block or approval | implemented (source); candidate gap; live unverified |
| Content safety and safe errors | All six operations and their error envelopes | Bundled content-safety policy; `mcp_api.py`; `v12_service.py`/`domain_api.py` error translation; registry safe-error metadata | Existing first-call safety test checks redaction for an internal dispatch error; it targets the old public catalogue in the current checkout | Candidate must send invalid, stale, cross-project, and malformed family calls and assert semantic safe errors, no traceback/private exception, and zero incomplete writes | Any `Cortex tool error`, validation error, traceback, or raw private data in coordinator/worker evidence fails live acceptance | source-tested; candidate gap; live unverified |
| Backend does not author questions, schedule workers, interpret answers, or auto-approve | Family-specific open/record operations only | `DecisionAggregate` is a persistence/transition boundary; scheduling and user policy remain coordinator-owned | Aggregate source shows no worker scheduler/question authoring; parity contract assigns these responsibilities to coordinator | Candidate black-box checks must assert records preserve user assertions and do not create workers, DAG revisions, or approvals as side effects | Live LLM must visibly author the question, answer it, approve the plan, and choose orchestration steps; transport/helper may not decide | implemented (source); candidate gap; live unverified |
| Transactional command receipt with build provenance | Each decision command produces one receipt; queries are receipt-free | `v12_store.py::run_command_receipt`; `domain_kernel.py`; `provenance.py`; candidate builder/runtime self-verification | `tests/test_command_receipts.py` and `tests/test_domain_kernel_receipts.py` cover generic atomic replay/conflict/build identity; `test_receipts_use_public_semantic_family_names` verifies the exact public command name for steering and every plan-review outcome. | Candidate must assert its stamped build ID/digest, exact public command name, one receipt per logical command, atomic rollback on failure, and exact replay after process restart | Live must print/observe the isolated candidate provenance before calls and must never use the stable profile or an unverified source process | source-tested; candidate gap; live unverified |
| Migration and maintenance compatibility for the v17 relation schema | Forward migration preserves historical rows; health checks detect the complete current schema | `v12_store.py::_migrate_plan_review_relations`; centralized `v12_maintenance.py::_REQUIRED_COLUMNS` contract | `tests/test_v17_maintenance_schema.py` proves a healthy v17 schema and a nominal v17 schema missing each relation column reports unhealthy and rejects maintenance actions safely. | Candidate/maintenance gate must migrate a v16-shaped store, validate all four v17 columns, and report unhealthy when any is absent | Live uses only a candidate whose migration/provenance and maintenance checks agree; no stable profile mutation | source-tested; candidate gap; live unverified |

The family rows above are retained only as traceability for earlier design
reviews. Current qualification must use the unified catalogue, the single
the narrow decision lifecycles, and the immutable assignment capability model
described at the top of this document.

## Cross-cutting candidate preservation invariants

These rows make explicit the broader invariants exposed by D-CAND-001 through
D-CAND-006. They remain blocked until the shared remediation and its complete
source, candidate, and live evidence are complete.

### Installed-candidate manifest-scope rule — 2026-08-29

The receipt-selected installed cache is an exact `plugins/cortex` payload, not
a copy of repository delivery metadata. Candidate qualification therefore uses
the declared plugin manifest scope while source/release gates retain full
marketplace/document/support-script validation. Any missing, extra, symlinked,
non-regular, or digest-mismatched installable plugin entry remains a hard
candidate failure. This resolves fixture setup provenance only; it does not
promote the installed-candidate or live status until the complete black-box
matrix is rerun.

### Canonical candidate-location rule — 2026-08-29

Every candidate-qualification branch consumes one typed location that is either
a complete release root or the exact installed plugin root from the verified
receipt. The location supplies the only server/runtime paths. A nested plugin
join, wrong topology, receipt disagreement, missing server, or source fallback
is a hard pre-dispatch failure; no branch may use its own path reconstruction.

| Cross-cutting capability | Owning boundary | Current evidence and root-cause document | Required evidence | Status |
| --- | --- | --- | --- | --- |
| Semantic error-code closure | Registry → Domain Kernel/store → service → MCP projection | `ErrorSpec` now owns every intentionally typed store/domain code; public operation metadata rejects aliases, unknown codes, and duplicate declarations. Focused source coverage scans all public runtime `code=` raises. | Exact candidate stdio must preserve each semantic code, safe action, and bounded details; only genuinely untyped faults may become `ledger_error`. | source-tested; candidate gap (D-CAND-001, D-CAND-005) |
| Contention-safe command liveness and receipt idempotency | Shared command-receipt executor and SQLite transaction boundary | Bounded retry plus exact-slot read-only reconciliation are centralized. Cross-process and deterministic forced-busy/lost-response/restart tests prove one mutation, replay, and changed-digest conflict. | Concurrent identical commands must converge to one receipt/binding with exact replay, bounded lock retry, and no replacement identity. | source-tested; candidate gap (D-CAND-002) |
| Publication-to-readiness capability closure | Plan publication, view materialization, and approval-relation producer | Relation validity now uses only immutable report/view digests, source sequence, task/project/type anchors, opaque handle, and consumption state; later unrelated chronology is ignored. | Candidate replay/open/record must preserve an issued relation after unrelated governance/initiative chronology, while changed views receive a distinct relation. | source-tested; candidate gap (D-CAND-003) |
| Typed relation-edge evidence | Decision result projection and closed public schemas | Family decisions and `publish_plan` now emit recursively closed compact refs/digests/relations only. Report and handles schemas are typed/closed, and canonical IDs are absent. | Candidate tools/list/runtime conformance must show exact closed structures and no canonical IDs. | source-tested; candidate gap (D-CAND-004) |
| Process-independent shard admission | All public commands before compact-ref verification and receipt execution | `V12Store` propagates one monotonic storage-admission deadline through WAL/pragmas, locator index read/repair/refresh, schema readiness/migration, compact-reference reads, and receipt transaction entry. The locator index is reconstructible/non-authoritative; post-commit refresh cannot revoke canonical output. Source `cortex.py` two-process MCP startup converges on one binding. | Two independent candidate stdio processes must converge on identical command/binding/receipt after forced first-open contention, with changed input conflicting. | Partial source-tested; P1 source-clearance blocked; candidate gap (D-CAND-006) |

| Pre-receipt shard admission and contention liveness | Shared shard/schema-readiness gate → SQLite connection/transaction entry → command-receipt executor | **Blocked by D-CAND-006 (P1).** `V12Store.for_task_ref` performs `_task_id_for_ref_suffix` and `_verify_known_task` (including `BEGIN IMMEDIATE`, automatic migrations/backfill, WAL setup, and sidecar finalization) before `run_command_receipt_resolved`; therefore the receipt retry/reconciliation boundary does not cover all public command admission. The current native 15-second SQLite wait is not coherent with `_write`'s 0.8-second budget. Root fix and acceptance criteria are in [phase-d-candidate-root-cause.md](phase-d-candidate-root-cause.md). | Source tests must force contention at resolution, WAL setup, schema readiness, and transaction entry; preserve safe corruption/schema errors; prove one generic gate for every public family and no cleanup error masking. | Exact candidate must run two independent real stdio processes issuing identical opens and prove same binding, one receipt/mutation, bounded completion, zero tool errors, then pass the complete suite before live-dev. | blocked (D-CAND-006; candidate/live unverified) |

### D-CAND-006 remediation re-review — 2026-08-29

The task-shard admission wrapper is present, but the parity row remains
**blocked (P1)**. Direct `record-locators.db` lookup/repair and some
post-resolution record-ref calls bypass the shared admission primitive; the
deadline is reset between `_task_store` and the receipt command; and one
write retry sleep is not bounded by remaining time. The focused synthetic
test does not establish two independent real-stdio processes converge through
these boundaries. Automatic forward migration and fail-closed validation are
retained, but candidate/live status is unchanged.

The final bounded source review supersedes earlier “R3/R4 blocked” notes:
R1–R4 are now source-supported, including chronology-independent immutable
relation replay and recursively closed no-canonical-ID family/publication
projections. Candidate/live evidence is still required for every row.

## Candidate contention gate update — 2026-08-29

The source-level R1–R4 review remains supported, but the candidate gate is now
blocked by a separate pre-receipt liveness defect. One of two independent
candidate stdio processes received `storage_unavailable` during identical
`open_clarification` calls. The parity row above is intentionally not promoted
by the existing direct receipt tests: they enter the receipt executor directly
and do not cover compact-reference resolution, schema readiness, WAL setup, or
sidecar cleanup. The candidate remains unverified until the generic admission
gate and full exact-candidate matrix pass; no live-dev result is claimed.

## D-CAND-006 final bounded source re-review — 2026-08-29

The shared-admission remediation remains **blocked (P1)**. The task-shard,
bootstrap, known-anchor, read, write, WAL/pragmas, and receipt paths now share
the helper, but direct `record-locators.db` lookup/repair and some post-
resolution/post-commit locator work still bypass it. The public command also
resets its deadline between `_task_store` and receipt execution, permits an
unclamped write retry sleep, and uses exception-text matching for journal-mode
busy handling. The synthetic source test does not establish independent
real-stdio convergence through those paths. Forward-only migration and
fail-closed validation remain required and intact; no candidate/live status is
promoted until the generic gate and complete candidate matrix pass.

## Final bounded R3/R4 source review — 2026-08-29

The latest production source and focused test surfaces support all four shared
remediation roots. Plan publication remains one durable report/relation/receipt
boundary; relation validity is independent of global/current timeline state;
unrelated chronology preserves replay/open/record; and changed view evidence
receives a distinct server-issued relation/binding. All six family decision
outputs and `publish_plan` are recursively closed, tools/list schemas match the
runtime projection, and centralized handles contain only compact refs and
opaque relation tokens. No new P0/P1 source finding was identified. Candidate
and required LLM-driven live-dev evidence remain open under D-ADV-013.

## Blocking findings at the time of this matrix

The production checkout contains the six family adapter functions and the
composition map, but evidence is not yet complete. The following are explicit
gates, not assumptions:

1. The public contract and registry cutover must remain internally consistent:
   no executable contract or instruction may reintroduce the retired overloaded
   boundary or a literal catalogue count. Source regressions cover this rule.
2. The exact-candidate stdio qualification must call all six operations from
   the advertised catalogue. The source test surface now uses the typed family
   pairs; it is still not candidate qualification.
3. Candidate qualification must cover localized byte-exact responses,
   immutable plan outcomes, steering supersession, process restart, and
   read-only reconciliation, not only clarification replay.
4. A real live-dev run remains unverified until the isolated candidate is
   refreshed by `scripts/cortex-dev`, ordinary Codex runs in the named default
   tmux session, and the LLM observes both coordinator and bounded worker
   structured events. No helper may answer questions, approve plans, parse
   acceptance, or classify a run autonomously.
5. Source remediation now maps every receipt to its exact public family
   operation name and captures the immutable plan/view/approval relation on
   the review binding. Candidate evidence must independently prove both rules
   against the advertised MCP contract.
6. Candidate qualification is currently blocked before process launch by
   generated runtime state rejected by the candidate builder (D-ADV-013).
7. Runtime and maintenance now share the complete v17 relation-column
   requirement in source. Candidate evidence must independently prove the
   forward migration and fail-closed maintenance behavior; the source gate for
   D-ADV-014 is clear.

## Promotion rule

The decision vertical slice is accepted only when every row has successful
source, exact-candidate, and required live evidence. A source pass cannot
override a candidate gap; a candidate pass cannot override a live tool error;
and a final decision reference cannot conceal an earlier validation error or
unexplained mutation replay. Any failure returns the row to `blocked` and
requires a root-cause change in the owning boundary, followed by the complete
evidence row again.
## Fresh candidate gate — 2026-08-29

The strict exact-candidate Phase D command was rerun from a fresh
content-addressed `1.12.1` package after R1–R4 remediation. Package hygiene was
clean before and after; no bytecode artifacts were present. The stdio process
had checkout `PYTHONPATH` and source mode removed and reported
`parityVerified=true`.

```text
PYTHONDONTWRITEBYTECODE=1 python3 -B -m pytest -q tests/test_phase_d_decision_qualification.py
4 passed, 6 failed, 0 skipped, 0 collection errors
```

No matrix row is promoted to candidate-verified. The candidate did pass
catalogue/provenance, concurrency convergence, restart replay, and advisory
no-scheduling checks. Remaining red cases are canonical error/result contract
alignment for changed/stale inputs, bounded steering supersession evidence,
and the complete plan-outcome parametrization. Candidate D-ADV-013 remains
open; live acceptance is still unrun.

## Focused live-dev retry status — 2026-08-29

The post-restart retry is **blocked before live evidence**. The supported
`cortex-live-smoke start --workdir <fresh-temporary-project>` path successfully
refreshed the isolated candidate, reconciled the isolated marketplace, staged
the content-addressed `1.12.1+codex.sha256.<id>` candidate, and installed it.
The launcher then looked only for an unstamped `1.12.1` cache directory during
its provenance check and exited before ordinary Codex launched. Therefore all
live columns remain `live unverified`; this is a delivery/provenance failure,
not evidence against the six decision operations. No prompt, clarification,
MCP call, worker, or hidden event stream was observed. The exact evidence and
the required shared-identity correction are recorded in
[Phase D focused live verification](phase-d-live-verification.md).

## Candidate mismatch adjudication — 2026-08-29

The six candidate mismatches were adjudicated against the authoritative source
contracts. They are qualification expectation drift, not reasons to weaken
production semantics: changed clarification and all three changed plan-review
outcomes correctly return canonical `command_conflict`; stale/cross-project
paths use canonical `command_conflict` and
`clarification_binding_not_found`; and steering returns a bounded nested
decision object with compact `decision_ref` plus typed
`relations.supersedes_decision_ref`, not canonical IDs. The minimal next gate
is a full rerun of the exact candidate suite after its allowlists and steering
assertions are aligned. No parity row is candidate-verified until that run is
green with zero failures and zero skips.

## Strict candidate gate result — 2026-08-29

After the adjudicated qualification-contract corrections, the complete fresh
candidate run reported **9 passed, 1 failed, 0 skipped, 0 collection errors**.
The sole failed row is concurrent identical `open_clarification`: one of two
real stdio candidate processes returned retryable `storage_unavailable`.
Thread failures are captured explicitly by the suite and cannot be hidden as
warnings. All other family operations and listed decision invariants passed,
but no row is promoted to candidate-verified while the concurrency gate is
red. D-ADV-013 remains open and live acceptance remains unrun.

## D-CAND-006 ContextVar remediation re-review — 2026-08-29

Source inspection now supports one inherited monotonic admission deadline
across task/record locators, shard/bootstrap readiness, WAL/pragmas, anchor
verification, reads/writes, receipt entry, and derived locator refresh. Numeric
SQLite busy detection, capped waits, forward-only migration, fail-closed schema
validation, and primary-error preservation are supported by source/focused
tests. No implementation P0/P1 was found.

The exact-candidate two-process MCP qualification is sufficient as the required
black-box candidate evidence once green: it uses two independent candidate
stdio processes and observes the actual public handlers. It does not replace a
source-mode two-process stdio regression for source clearance because the
current source tests are in-process/direct-store only. The parity row remains
source-supported but evidence-blocked until that bounded source MCP test and
the complete exact-candidate suite pass with zero failures/skips; live-dev
remains unrun.

## D-CAND-006 ContextVar final bounded adjudication — 2026-08-29

Source evidence supports inherited admission across compact locators,
task/record resolution, shard/bootstrap/migration readiness, WAL/pragmas,
anchor verification, reads/writes, receipt entry, numeric SQLite busy
classification, capped waits, and primary-error-preserving cleanup.

The parity row remains **P1 / source-clearance blocked** because the public
`_mutation` path calls `_sync_record_locators` after `_write` has reset the
admission context. A derived locator refresh may fail after the canonical
mutation commits and surface a new-budget error to the caller. The command
boundary must include this work or explicitly make it non-authoritative and
non-failing; do not add a tool-specific retry.

The existing two-process exact-candidate MCP test is sufficient black-box
candidate evidence when green. A separate bounded two-process source-stdio
test remains required for source clearance. No candidate/live-dev evidence is
promoted by this review; next promotion requires the architectural scope fix,
source integration test, and a candidate run with zero failures and skips.

## D-CAND-006 Final locator-authority and source-stdio review — 2026-08-29

The latest `_mutation` boundary now wraps the complete legacy mutation and
makes post-commit locator refresh best-effort. A refresh failure therefore
cannot revoke or misreport a committed canonical result. The refresh itself
is bounded and idempotent because it rebuilds derived rows from canonical
shard records; normal indexed resolution still verifies the canonical row.

The parity row remains **P1 / source-clearance blocked**. In
`_for_record_ref_once`, a valid canonical result found by the fallback shard
scan is followed by synchronous `_sync_record_locators()`. If that derived
repair fails, the canonical record is not returned. `_record_locator_matches`
also turns sidecar schema/read failures into `storage_unavailable` instead of
treating the sidecar as unavailable and falling back. Repair must be
best-effort; canonical shard schema/security failures remain fail-closed.

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

The parity row remains **P1 / source-clearance blocked**. During
`_for_record_ref_once`, a canonical record found by fallback scan is followed
by synchronous `_sync_record_locators`; repair failure prevents the verified
record from being returned. `_record_locator_matches` maps sidecar schema/read
failures to `storage_unavailable` rather than falling back, and the initial
sidecar rebuild in `_bootstrap_once` can abort construction after canonical
schema commit. Sidecar repair must be best-effort/reconstructible; canonical
shard schema/security errors remain fail-closed.

The source stdio test genuinely covers two source `cortex.py` processes,
initialization, compact task resolution, shard readiness, and identical open
convergence, but not the receipt-row assertion, changed-input conflict, or
record-locator fallback/repair. Add those bounded MCP assertions before source
clearance; the exact-candidate gate remains separate. No candidate/live-dev
execution was performed.

## D-CAND-006 read-only source re-review after host restart — 2026-08-29

The focused source suite passed with bytecode disabled (`29 passed, 16
subtests passed`), but the parity row remains **P1/source-clearance blocked**.
The derived locator index is not yet non-authoritative at every boundary:
fallback record resolution requires synchronous repair even after canonical
verification, invalid/unreadable sidecar state is surfaced as
`storage_unavailable` instead of falling back, and initial sidecar rebuild can
abort store construction after canonical schema commit. The two-process source
MCP regression proves initialization, compact task resolution, shard
readiness, identical open success, and one binding row; it does not prove one
receipt row, changed-input conflict, or locator fallback/repair. No candidate
or live-dev evidence is promoted by this review.

## D-CAND-006 independent post-remediation race review — 2026-08-29

The strengthened source MCP scenario now asserts identical binding, exactly
one binding row, one open receipt, one record mutation/receipt, and changed
input `command_conflict`; direct locator-authority injections pass as well.
The parity row remains **P1/source-clearance blocked** because repeated
independent process admission intermittently returns `storage_unavailable`
from `_materialize_sidecars`: SQLite can unlink `cortex.db-wal` after the
sidecar check and before the owner-only `os.chmod()` call. This is a
canonical WAL/SHM housekeeping race outside the effective busy retry boundary.
The generic admission fix must absorb that race within the inherited deadline
while preserving fail-closed unsafe-path and canonical-schema behavior. No
candidate/live evidence is promoted.

## Locator-authority source update — 2026-08-29

`record-locators.db` is now uniformly reconstructible/non-authoritative. Its
malformed, unreadable, stale, or repair-failed state falls back to canonical
shard verification and cannot revoke a verified record or a canonically ready
bootstrap. Source `cortex.py` MCP evidence now includes one binding, one
matching open receipt, and changed-response `command_conflict` without a
duplicate mutation. The status is source-supported; the exact candidate and
live rows remain unverified and are not downgraded by these tests.

## D-CAND-006 final WAL/SHM stress adjudication — 2026-08-29

The locator-authority injections and successful two-process assertions pass,
but the parity row remains **P1/source-clearance blocked**. Repeating the
four-round source-MCP stress 20 times (80 races) produced a child with no
JSON-RPC response (stdout closed); the peer returned a replayed success. An
independent 80-race harness also observed a child exit `-7` (SIGBUS). This
violates the no-hidden-response/no-crash admission invariant. The generic
WAL/SHM and lock boundary requires another root correction before candidate or
live evidence can be promoted.

## D-CAND-006 final independent source adjudication — 2026-08-29

The locator-authority injections and strengthened two-process assertions pass
on successful runs, but the parity row remains **P1/source-clearance blocked**.
Repeated independent admission can still return `storage_unavailable` from
`_materialize_sidecars` when SQLite removes `cortex.db-wal` after validation
and before `os.chmod()`. This canonical WAL/SHM race is outside the effective
busy retry boundary. The generic admission implementation must absorb the
race within the inherited deadline while preserving fail-closed unsafe-path
and canonical-schema behavior. No candidate/live evidence is promoted.

## D-CAND-006 WAL/SHM parity update — 2026-08-29

The source parity blocker for canonical WAL/SHM admission is cleared. Cortex
does not create SQLite-owned sidecars; it only safely normalizes an extant
regular file and treats a verified disappearance as an ephemeral SQLite state.
A shared descriptor-validated per-shard admission lock serializes WAL-mode
setup under the existing propagated deadline, while command receipts and
ordinary transactions remain SQLite-authoritative. The source two-process MCP
test and four-round stress prove binding/receipt convergence and no duplicate
mutation. This changes only the source row: exact-candidate and live-dev rows
remain unqualified.

## SQLite sidecar final parity update — 2026-08-29

Source parity now includes generic connection-lifetime per-shard lease
serialization. It preserves receipt identity while preventing divergent live
SQLite sidecar views; Cortex does not mutate WAL/SHM on commit or close. An
exit-code-aware 80-pair source MCP stress is green. Exact-candidate and
live-dev parity rows remain unqualified.

## D-CAND-006 final independent WAL/SHM stress adjudication — 2026-08-29

The locator-authority injections and successful two-process assertions pass,
but the parity row remains **P1/source-clearance blocked**. Repeating the
four-round source-MCP stress 20 times (80 races) produced a child with no
JSON-RPC response (stdout closed); the peer returned a replayed success. An
independent 80-race harness also observed a child exit `-7` (SIGBUS). This
violates the no-hidden-response/no-crash admission invariant. The generic
WAL/SHM and lock boundary requires another root correction before candidate or
live evidence can be promoted.
## D-CAND-006 final connection-lifetime lease review — 2026-08-29

| Capability/invariant | Source evidence | Status |
|---|---|---|
| Per-shard lease before connect through transaction/close | Active `_connection` path plus reentrancy, release, process-exit, and shard-local tests | Pass |
| One bounded admission deadline and numeric SQLite contention | Focused deadline/error tests and source suite | Pass |
| Canonical authority independent of locator sidecar | Locator failure-injection and fallback tests | Pass |
| Two-process source MCP convergence | 80 simultaneous pairs; clean exit/stderr-aware helper; same binding and one open receipt | Pass |
| One record mutation/receipt and changed-input conflict | Dedicated real source stdio scenario | Pass |
| No Cortex mutation of live WAL/SHM anywhere in source | Dormant `_secure_sqlite_sidecar` still calls `os.fchmod` | **P1 fail** |

The final row prevents source clearance despite green active-path stress.  The
sidecar helper must be removed or made validation-only, and the no-mutation
property must be asserted directly.  Candidate/live-dev qualification is not
claimed by this source review.

## Latent-path parity closure — 2026-08-29

The source now contains no mutating WAL/SHM helper, and AST conformance fails
any reintroduction. The source parity invariant is cleared; candidate/live
rows remain unqualified.
## D-CAND-006 final post-removal clearance check — 2026-08-29

| Capability/invariant | Evidence | Status |
|---|---|---|
| No current store helper mutates WAL/SHM | Removed helper; source inventory finds validation-only sidecar functions | Pass |
| Canonical DB protection remains separate | `_protect_canonical_database` is distinct from sidecar validation | Pass |
| Non-bypassable no-mutation qualification guard | Current AST test is literal-text and misses aliases/computed paths/indirection | **P1 fail** |
| 80-pair source-MCP stability | 80 simultaneous pairs, clean exit-aware completion | Pass |

The guard weakness prevents source clearance despite green current behavior.
Candidate/live-dev qualification is not claimed.

Package-wide mutation-capability conformance now enforces validation-only live
SQLite sidecars; candidate/live status remains unqualified.

## Enforceable source boundary — 2026-08-29

| Capability/invariant | Evidence | Status |
|---|---|---|
| Package-wide mutation boundary | Runtime modules and `cortex.py` are checked against exact reviewed capability/purpose pairs | Pass |
| Bypass resistance | Computed suffix, alias, helper, pathlib, and dynamic-lookup fixtures fail conformance | Pass |
| Live SQLite sidecar capability | Validation only; no mutation capability is registered | Pass |
| Dynamic source-process observation | 80-pair MCP stress records no Cortex Python mutation of `-wal`/`-shm` | Pass |
| Exact candidate / LLM live-dev | Not run in this source-only task | Unqualified |

| Returned constructor/module identity | Path/module return summaries propagate through assigned and chained indirect calls | Pass |

| Recursive alias/dataflow resistance | Imports, assignment aliases, nested modules, defaults, closures, and dynamic lookup fail closed | Pass |
| FD-level live-sidecar observation | Resolved descriptor targets are checked for write/pwrite/ftruncate/truncate | Pass |
## D-CAND-006 final filesystem-boundary review — 2026-08-29

| Capability/invariant | Evidence | Status |
|---|---|---|
| Active live WAL/SHM handling is validation-only | Source inventory and 80-pair runtime stress | Pass |
| Exec-child observer is active | Separate child probe writes an observed sidecar-path event | Pass |
| Package policy catches all direct/aliased/pathlib/helper mutation forms | Assignment aliases, aliased pathlib modules, and nested modules bypass current checker | **P1 fail** |
| Observer covers every relevant mutation primitive | `os.write`/`os.ftruncate` and similar low-level paths are not wrapped | **P1 fail** |
| CODEX_HOME isolation | Source helper sets isolated `CODEX_HOME` and removes `PYTHONPATH` | Pass |

The two P1 control gaps prevent source clearance despite green current-path
behavior.  Candidate/live-dev qualification is not claimed.
## D-CAND-006 final filesystem-policy adversarial review — 2026-08-29

| Capability/invariant | Evidence | Status |
|---|---|---|
| Recursive package scope and common alias/pathlib/default/closure rejection | Current policy and negative fixtures | Pass |
| Path and FD observer coverage | Child observer covers path mutation plus `write`, `pwrite`, `ftruncate` | Pass |
| Subscript dynamic lookup rejection | `os.__dict__["unlink"](...)` bypasses current checker | **P1 fail** |
| Helper/callback callable-flow rejection | Return/callback/partial-carried mutators bypass current checker | **P1 fail** |
| Source MCP stability and isolated execution | 80-pair stress, isolated `CODEX_HOME`, clean child exits | Pass |

The two remaining proof-boundary gaps prevent source clearance.  Candidate and
live-dev qualification is not claimed.

## Final P1 proof-boundary closure — 2026-08-29

| Capability/invariant | Evidence | Status |
|---|---|---|
| Dynamic subscript call targets | OS/pathlib/shutil dictionary chains are checked before generic call dispatch | Pass |
| Callable export/escape | Return, yield, containers, attributes, partial, callbacks, defaults, and closures fail closed | Pass |
| FD identity coverage | Realpath resolution and dup/dup2 propagation protect FD mutation observation | Pass |
| Source MCP stability and isolation | 80-pair stress, isolated `CODEX_HOME`, clean child exits | Pass |
| Exact candidate / LLM live-dev | Not run in this source-only task | Unqualified |
## D-CAND-006 final callable-flow review — 2026-08-29

| Capability/invariant | Evidence | Status |
|---|---|---|
| Subscript, return/yield, partial, callback, container, attribute, decorator, closure, nested-helper rejection | Expanded negative fixtures pass | Pass |
| FD dup and write-family observation | Child observer wraps `dup`, `dup2`, `write`, `pwrite`, `writev`, `pwritev`, `ftruncate` | Pass |
| Helper-returned pathlib constructor rejection | `helper()(path).unlink()` remains accepted | **P1 fail** |
| Source-MCP stability and isolation | 80-pair stress and isolated `CODEX_HOME` pass | Pass |

The remaining callable-flow gap prevents source clearance.  Candidate and
live-dev qualification is not claimed.
## D-CAND-006 final declared-scope clearance — 2026-08-29

| Capability/invariant | Evidence | Status |
|---|---|---|
| Returned constructor/module propagation | Direct, assigned, one-hop, and two-hop `pathlib.Path`/module fixtures reject mutators | Pass |
| Subscript/callback/storage and nested scope coverage | Expanded negative fixture set passes | Pass |
| FD observer through duplication and write family | `dup`/`dup2`, `write`/`pwrite`/`writev`/`pwritev`/`ftruncate` wrappers plus self-probe | Pass |
| Source MCP stability and isolated state | Exit-aware 80-pair stress; isolated `CODEX_HOME`; no duplicate receipts | Pass |
| D-CAND-006 source status | All declared Python/runtime invariants green | **Source-cleared** |

Exact-candidate and live-dev qualification are separate, unrun release gates.

## Live-dev evidence update — 2026-08-29

The focused operator-controlled live attempt is recorded in
[phase-d-live-verification.md](phase-d-live-verification.md). The session
failed before Codex readiness during isolated marketplace registration, so
the live rows remain **Unqualified**. No decision operation, worker report, or
tool result was observed in that attempt.

## Packaging closure parity — 2026-08-29

The complete production runtime boundary is represented by the canonical
`plugins/cortex/runtime-payload.json` manifest and shared
`scripts/cortex_payload_manifest.py` implementation. Both candidate staging
and marketplace validation recursively cover nested runtime modules and
package initializers, reject unsafe/non-regular entries, and require exact
file and directory-topology parity. This includes `filesystem_policy.py` and
rejects extra empty directories. Candidate/plugin/repository root symlinks are
rejected before resolution. The manifest and topology participate in the
content-addressed identity; focused tests prove identical source builds have
identical IDs. This is package evidence only; Decision candidate and live-dev
acceptance remain separate gates.

Packaging parity includes trusted ancestry: the shared lstat chain rejects
symlinked cache/version/plugin ancestors before staging, reuse, validation, or
digesting, and safe creation revalidates after creating missing directories.
The release and marketplace gates share exact plugin-tree topology, including
undeclared-empty-directory rejection. This preserves the same executable
runtime boundary for every orchestration capability before Decision candidate
or live-dev qualification.

## Candidate payload parity — 2026-08-29

The installable runtime boundary now has one explicit parity source:
`plugins/cortex/runtime-payload.json`. It covers the launcher and every
production `cortex_runtime` module, including `filesystem_policy.py`. The
candidate builder and marketplace validator compare the declared closure with
the actual source/candidate tree and fail closed on missing or unlisted
modules. The closure is included in the content-addressed digest, so source
and candidate qualification cannot silently execute different runtime code.
Focused packaging tests cover staging/importability, unlisted-module
rejection, and deterministic build identity. This is source/package evidence;
the Decision candidate and live-dev rows remain independently unqualified.

## Final exact-candidate parity evidence — 2026-08-29 (supersedes prior status)

The fresh staged candidate `1.12.1+codex.sha256.eb691a9a49377dcc` passed the
complete Decision candidate gate with `parityVerified=true`: **11 passed, 0
failures, 0 skips, 0 errors**. The candidate harness removed checkout
`PYTHONPATH` and `CORTEX_SOURCE_MODE`, used isolated state, and executed the
staged stdio server only.

The 80-pair candidate stress passed for all decision-family invariants: one
server-owned binding/open receipt, one record mutation/receipt, exact replay,
changed-input `command_conflict`, no duplicate mutation, no child crash or
forced termination, no hidden EOF/stderr, and no observed Python-side
WAL/SHM sidecar. Clarification localization/byte preservation, plan-review
outcomes, steering/supersession, stale/cross-project safety, restart
reconciliation, advisory governance, and closed catalogue/provenance rows are
now candidate-verified. Live remains `live unverified` until the focused
LLM-driven live-dev gate completes.

## Live observation transport correction — 2026-08-29

The live row remains **unverified**. The only completed work is a
transport-only correction for the detached-Codex observation defect: the exact
named pane receives an owner-only, bounded, output-only `pipe-pane` capture
before the ordinary isolated launcher is released. This makes a trust/composer
alternate screen visible to the LLM without the helper deciding what it means.

The helper now also has a single-key `enter` action scoped to that exact pane.
It may be used only after the LLM/operator visibly observes the fresh-project
acknowledgement. It never auto-trusts, writes Codex trust configuration,
submits a workload, answers a clarification, approves a plan, parses tool
output, or decides acceptance. Unit evidence covers ordering, bounded capture,
permissions, visibility, exact targeting, missing-session behavior, and
cleanup; it is not live orchestration evidence. The next required step remains
the full LLM-driven decision scenario with worker-event inspection.

## Shared-state compact-task resolution — v19 source evidence

Task anchoring remains a server-owned capability used by every task-anchored
operation; no public tool, schema, coordinator instruction, worker policy, or
orchestration responsibility changed. `v19-derived-task-locators` adds a
derived root-local route index backed by an atomic canonical per-shard task
publication. The resolver verifies the claimed shard and canonical task row
before any open/read/decision/assignment/publication/governance/closure path
uses the result. Missing, stale, corrupt, or wrong-project index material is
not authority: one bounded canonical recovery scan either proves and repairs
one mapping or fails closed on no match/ambiguity/cross-project state.

The source topology regression creates 80 project shards under one state root
and starts 160 fresh concurrent compact-task first calls. All resolve their
own canonical task with no `storage_busy`; the normal path is separately
proved not to call the legacy all-shard scan. This preserves the existing
record-locator rule, same-worker clarification continuation, receipt/replay
semantics, and all 15 public tools. Installed-candidate and live evidence are
still required and are not claimed by this source result.

## Dispatch correlation evidence — v20 source boundary

Assignment dispatch now emits one server-owned random observational marker and
its one-way fingerprint in the trusted brief. The same immutable evidence is
returned with an assignment-origin clarification handoff; the journal stores
only a one-way fingerprint. It does not authorize host work, prove a native
process started or resumed, alter publication reconciliation, add an MCP tool,
or become a call argument. The later host adapter must still prove its own
native lifecycle transitions; until then the durable result is handoff
evidence and coordinator-owned recovery, not claimed worker continuation.
