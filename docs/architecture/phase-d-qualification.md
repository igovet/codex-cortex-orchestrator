# Phase D decision vertical-slice qualification

Status: **blocked**. The exact staged candidate decision suite is now green,
but the latest required attached-client live gate failed/unverified: the
visible clarification had no observed `open_clarification`/
`record_clarification` MCP pair and no accepted first worker report event.
No candidate or live-dev pass is claimed from that run.

The later focused LLM-driven session is also failed/unverified, but at an
earlier boundary: it reached a confirmed composer and one workload submission,
then produced no first Cortex MCP event. See
[Phase D focused live result](phase-d-live-result.md). This does not replace
the clarification/worker acceptance matrix; it blocks another live attempt
until the no-first-event startup/route handoff is corrected. The read-only
diagnosis is [Phase D live first-call root cause](phase-d-live-first-call-root-cause.md):
candidate delivery and standalone discovery are proven, while the ordinary
session did not issue a Cortex call. The required next change is a host-owned
registration receipt plus a safe server-ready observation, not prompt-level
MCP parameter teaching.

The first post-boundary retry reached the attached composer but failed the
pre-workload receipt gate: no exact-session `server_ready` registration event
was observed. It is consequently a live infrastructure failure before the
first semantic task operation, not evidence about clarification, worker,
publication, or decision correctness. See [Phase D focused live result](phase-d-live-result.md).
The exact propagation root cause and the required runtime-owned replacement are
in [Phase D live server-ready root cause](phase-d-live-server-ready-root-cause.md).

The independent hold/event-journal source review is recorded in
[phase-d-hold-event-review.md](phase-d-hold-event-review.md). It withholds
source clearance: the focused source suite is red and the MCP composition
boundary has validation and wire-outcome observation gaps. Candidate and
live-dev remain unrun by that review until the six P1 findings are closed.

For D-CAND-006 specifically, source clearance is also withheld pending the
exit-code-aware WAL/SHM stress gate. The canonical root-cause and lifecycle
decision is [Phase D SQLite SIGBUS root cause](phase-d-sqlite-sigbus-root-cause.md).
It supersedes historical contradictory status notes: a bounded source harness
has passed one 160-process reproduction, but no exact-candidate or live-dev
pass is claimed.

Phase D qualifies the decision transaction through the staged candidate's real
stdio MCP process. It is deliberately separate from direct store tests: the
process is launched from the candidate package, ambient `PYTHONPATH` and
source-mode flags are removed, and the launcher's build identity is checked
against the candidate package digest.

The executable suite is
`tests/test_phase_d_decision_qualification.py`. It builds a temporary
content-addressed candidate with `build_source_candidate`, launches the actual
`plugins/cortex/scripts/cortex.py` stdio handler, and uses only JSON-RPC
`initialize`, `notifications/initialized`, `tools/list`, and `tools/call`.
The candidate harness does not import candidate runtime modules, use a source
checkout path, or infer an opaque reference from text. Every later reference
is copied from the preceding structured result.

## Invariants

| Invariant | Qualification evidence |
| --- | --- |
| The public boundary has one semantic catalogue | `tools/list` returns exactly the fifteen operations in the registry and none of the retired overloaded decision operations. |
| Candidate provenance is authoritative | `initialize.serverInfo` reports parity verification, candidate path, source digest, and the build ID derived from the staged package. |
| A logical family operation has one server-owned binding | Repeated identical `open_clarification`, `open_plan_review`, or `open_steering` calls return the exact same binding handle. |
| A producer handle is consumed byte-for-byte | Each record call receives the exact scalar `binding_ref` copied from its matching open result. |
| Recording is atomic and replay-safe | Exact response replay returns the original decision reference and replay classification; a changed response/outcome is a conflict. |
| Family boundaries are closed | A clarification binding cannot be consumed by plan review; malformed extra fields fail schema validation before dispatch. |
| Plan outcomes preserve the immutable relation | The qualification cases require approval, revision request, and cancellation against one server-issued plan/view relation; source review is clear for the persisted relation, but candidate evidence still fails because semantic publication does not deliver a ready approval relation (D-CAND-003). The shared root and acceptance contract are recorded in [phase-d-candidate-root-cause.md](phase-d-candidate-root-cause.md). |
| Steering changes the effective contract once | A non-empty steering delta creates one next revision; explicit supersession is preserved, and replay does not create another revision. |
| Recovery never opens a replacement decision | After commit and process restart, reopening returns the original binding and recording the original response is an exact replay. |
| Cross-project and stale references fail closed | Stale contract bindings, wrong-family bindings, and bindings used in another project return semantic safe errors and do not mutate state. |
| Backend remains non-authoritative for orchestration | Decision calls preserve the user assertion without creating a worker, scheduling a DAG, or silently approving a plan. |
| Errors are safe | Tool errors contain bounded semantic codes/actions without tracebacks, private exception text, or raw diagnostic data. |

## Qualification matrix

| Case | Required result | Status gate |
| --- | --- | --- |
| Fresh task and six-operation catalogue | First calls succeed from the advertised schemas | Required |
| Clarification open/record | Localized prompt and response are retained byte-for-byte; one decision is committed | Required |
| Same logical open | Same pending binding, no new row | Required |
| Exact record replay | Same decision and receipt, replay classification | Required |
| Changed response | Conflict, no second mutation | Required |
| Stale contract revision | Precise stale result, no new binding | Required |
| Wrong family/type | Fail closed, no decision mutation | Required |
| Cross-project handle | Fail closed without information leakage | Required |
| Lost response after commit | Read-only recovery/replay finds the consumed decision | Required |
| Process restart | Same database state and same binding/decision | Required |
| Concurrent open/record | One logical binding and one decision; one result is an exact replay | Required |
| Plan approval | Ready plan relation is server-issued and consumed once | Required |
| Request revision/cancel | Each outcome persists distinctly against the same immutable plan/view relation | Required |
| Steering intent | Delta persists as one effective-contract revision with supersession | Required |
| Receipt provenance | Every family receipt names the exact public operation, including steering and all plan-review outcomes | Source-tested; candidate evidence required |
| v17 migration and maintenance health | Forward migration preserves historical rows and maintenance rejects missing plan-relation columns | Source-tested; candidate evidence required |
| Governance and backend ownership | Advisory governance remains nonblocking; decision calls do not schedule or auto-approve | Required |
| Hold/event-journal source gate | v18 hold atomicity, exact-assignment reconciliation, renderer integrity, all `tools/call` observation paths, bounded private journal, and transport-only live helper | **Blocked — see phase-d-hold-event-review.md; six P1 findings** |

The matrix is intentionally broader than direct store coverage. A row is not
complete when a façade or store test passes; it requires an exact-candidate
stdio regression and durable-state assertion. Any unimplemented or blocked row
must be reported as unrun or blocked, never silently skipped.

## Process boundary

The black-box harness sends only MCP JSON-RPC frames. It uses separate
candidate processes for concurrency, the same project directory for durable
state, and an explicit restart for recovery. The candidate environment removes
`PYTHONPATH` and `CORTEX_SOURCE_MODE`, sets the expected build identity, and
requires `parityVerified=true` before semantic calls.

This phase does not own orchestration policy: worker selection, DAG changes,
rework decisions, governance depth, user questions, approvals, and final
synthesis remain coordinator-owned capabilities listed in the parity matrix.
Phase D preserves those capabilities while proving that the typed decision
boundary cannot create duplicate bindings or decisions.

## Delivery precondition

Exact-candidate stdio evidence proves a staged package, but ordinary live-dev
uses the native isolated installation cache. Before a focused live decision
run, the supported sync must atomically commit its authoritative installed
candidate receipt and `scripts/cortex-dev` must consume that receipt's stamped
candidate path rather than infer a path from the base product version. The
receipt validation is a separate fail-closed delivery gate for source/candidate
digest parity, lexical non-symlinked path ancestry, isolated-target identity,
and immutable manifest/version agreement. It does not replace a real
LLM-observed live session; it only establishes that such a session is testing
the package qualified here.

The installed-cache fixture uses the receipt-selected plugin root and the
declared installable-plugin manifest only. Repository delivery artifacts remain
mandatory for source/release validation but are intentionally absent from the
native plugin cache. This scope distinction preserves strict exact payload
verification—missing, extra, altered, non-regular, or symlinked plugin files
still reject qualification—without requiring a cache to impersonate a source
checkout.

## Evidence rule

The suite must run first against source-level checks, then against the exact
staged candidate. The candidate gate is not a live-dev result. No Phase D row
may be promoted to candidate-verified until the complete suite passes with zero
skips and no generated bytecode artifacts. Live acceptance remains a separate
LLM-driven ordinary Codex/tmux gate and must inspect coordinator and worker
structured evidence.

## Exact-candidate run — 2026-08-29

Package hygiene was checked before staging. The only discovered bytecode state
was three empty generated directories under `plugins/cortex/scripts/`,
`plugins/cortex/scripts/cortex_runtime/`, and `tests/`; no `.pyc` or `.pyo`
files remained after removing those exact directories. The state was generated
by earlier Python/pytest inspection of the checkout, not by candidate runtime
data. Qualification commands use both `PYTHONDONTWRITEBYTECODE=1` and
`python3 -B`; a post-run inventory found no bytecode files or directories.

The candidate was built with `build_source_candidate` into a private temporary
staging directory from this checkout. Its stamped candidate/source digest was
`sha256:4d678e846ba23f4fa00f75dab7c9ca8e34a38267257f145d02843ba8ad5e191e`.
The stdio process removed `PYTHONPATH` and `CORTEX_SOURCE_MODE`, supplied the
expected digest, and reported `parityVerified=true`. No installed profile,
stable `HOME`, or live-dev session was touched.

Command:

```text
PYTHONDONTWRITEBYTECODE=1 python3 -B -m pytest -q tests/test_phase_d_decision_qualification.py
```

Result: **5 passed, 5 failed, 0 skipped, 0 collection errors**. The failures
are architectural evidence and intentionally remain strict:

| Failing invariant | Observed candidate result | Required owner action |
| --- | --- | --- |
| Changed-response conflict | A changed response after an exact replay is returned as generic `ledger_error`, not the advertised semantic conflict code. | Preserve the aggregate conflict code through the public error translation. |
| Concurrent open | One of two real candidate processes receives retryable `storage_unavailable`; the first-call operation is not reliably convergent. | Repair candidate transaction/busy handling so the same logical open completes deterministically without an unexplained first-call error. |
| Plan relation | `publish_plan` does not expose a ready `approval_view`; the subsequent plan-review relation is unavailable. | Make semantic publication/open bind the exact immutable plan/view relation before any review outcome. |
| Steering supersession evidence | The durable relation is present only as a canonical internal ID; the public result/projection does not expose a typed relation comparable with the server-issued decision reference. | Return a bounded typed supersession relation in public evidence. |
| Stale contract error | Recording a binding after a contract revision is returned as generic `ledger_error`, not the advertised stale semantic error. | Preserve stale-binding classification through MCP error translation. |

The five passing cases prove candidate provenance/catalogue, clarification
first-call and exact response persistence, concurrent record convergence after
open, restart replay, steering revision/replay, and advisory/no-scheduling
side-effect behavior. They do not override the five failures above. Phase D
remains blocked until the owning source changes are made and this same exact
candidate command passes with zero failures and zero skips.

## Corrected strict requalification — 2026-08-29

The adjudicated assertion corrections were applied only in
`tests/test_phase_d_decision_qualification.py`: changed clarification and
plan-review intent now require `command_conflict`, cross-project lookup
requires `clarification_binding_not_found`, stale clarification requires
`clarification_binding_stale`, and steering requires the bounded nested
decision with compact supersession relation while rejecting canonical IDs.
No production code was changed.

The fresh candidate was rebuilt and run with bytecode disabled, checkout
imports removed, source mode removed, strict provenance checks, and
`parityVerified=true`.

```text
PYTHONDONTWRITEBYTECODE=1 python3 -B -m pytest -q tests/test_phase_d_decision_qualification.py
9 passed, 1 failed, 0 skipped, 0 collection errors
```

The corrected concurrency test now captures worker-thread failures instead of
allowing a pytest warning to masquerade as a pass. One of two real candidate
processes returned retryable `storage_unavailable` during identical concurrent
`open_clarification` calls. This is a production transaction/busy-handling
defect, not an assertion mismatch. All other decision-family, provenance,
restart, plan, steering, safety, and non-scheduling checks passed. The exact
candidate gate remains blocked until concurrent identical opens complete
without a first-call storage error and the same command returns zero failures.

## Fresh exact-candidate requalification — 2026-08-29

The package-preflight inventory was clean before this run: no `.pyc`, `.pyo`,
or `__pycache__` artifacts existed under `plugins/` or `tests/`. The candidate
was rebuilt from the current source with the repository candidate builder. The
stdio process ran with `PYTHONDONTWRITEBYTECODE=1`, `python3 -B`, checkout
`PYTHONPATH` removed, and `CORTEX_SOURCE_MODE` removed. Its candidate identity
was verified as `parityVerified=true` with build/source digest
`sha256:c84a4ae3c20534694e55de9749081bb50291773457963cd088ad8da2026d3e7a`.
No installed profile or live-dev session was touched.

Command:

```text
PYTHONDONTWRITEBYTECODE=1 python3 -B -m pytest -q tests/test_phase_d_decision_qualification.py
```

Result: **4 passed, 6 failed, 0 skipped, 0 collection errors**. The run is
not candidate-verified. The concurrent-open case now converges, and the
catalogue/provenance, restart replay, and advisory/no-scheduling cases pass.
The remaining failures are recorded without relaxing assertions:

| Case | Sanitized observation | Gate status |
| --- | --- | --- |
| Clarification changed-response conflict | The candidate now returns the semantic `command_conflict` class; the qualification test's accepted-code set has not yet been updated to include that canonical class. | Test-contract alignment required; not accepted. |
| Plan review changed-outcome conflict | The candidate returns `command_conflict`; the qualification test expects the older family-specific conflict aliases. | Test-contract alignment required; not accepted. |
| Plan approve/request-revision/cancel | All three first record/replay paths reach the plan relation, but each fails only on the changed-outcome assertion above. | Not accepted until the complete parametrized case is green. |
| Steering supersession | The candidate completes the effective revision/replay path. The bounded nested decision object is present, but the qualification test reads canonical `decision_id`/`supersedes_decision_id` fields that the closed projection intentionally omits. | Update assertions to use `decision_ref` and `relations.supersedes_decision_ref`; not accepted until the complete case is green. |
| Stale/cross-project safety | Stale recording is returned as the safe `command_conflict` class and cross-project recording as `clarification_binding_not_found`; the test's accepted-code sets are stale. | Test-contract alignment required; not accepted. |

These observations do not constitute a candidate pass: the full suite remains
red and the six decision operations are not promoted. No live-dev run is
permitted until the qualification contract is reconciled with the advertised
canonical result/error classes and the same command returns zero failures and
zero skips.

## Candidate failures are shared-root blockers — 2026-08-29

The original five failures above are not five independent implementation patches. The
bounded source review groups them into four architectural roots: one semantic
error taxonomy/projection (D-CAND-001 and D-CAND-005), a contention-safe
command-receipt executor (D-CAND-002), publication-to-readiness capability
closure (D-CAND-003), and one typed public relation projection (D-CAND-004).
The later strict rerun isolated D-CAND-006, a separate pre-receipt storage
admission/liveness defect documented at the end of this file.
See [phase-d-candidate-root-cause.md](phase-d-candidate-root-cause.md) for
affected source boundaries, acceptance criteria, and required source/candidate
tests. Candidate and live gates remain blocked; no candidate/live result is
claimed by this review.

## Remediation source re-review — 2026-08-29

The source remediation review does not grant clearance. The shared receipt
executor and bounded safe-detail projection are structurally supported, but
the registry still omits typed store codes, plan publication still performs
view/approval materialization after the report commit, and the steering/public
schemas still expose canonical IDs and a permissive publication envelope.
These are P1 source blockers recorded with exact evidence and acceptance
criteria in [phase-d-candidate-root-cause.md](phase-d-candidate-root-cause.md).
The exact candidate suite and live-dev gate remain unrun after remediation.

## Final exact-candidate qualification — 2026-08-29

The packaging and source clearances completed before this run. A fresh
content-addressed candidate was staged through the repository-supported
isolated sync path with product/plugin/server base version `1.12.1`:

```text
candidate version: 1.12.1+codex.sha256.eb691a9a49377dcc
candidate/source digest: eb691a9a49377dcc24640d415c5fa38d8e94b7cb33c104277443c5c3004c453f
marketplace validation: passed
release validation: passed; files=94
runtime parity: parityVerified=true
```

The candidate subprocesses executed the staged `plugins/cortex/scripts/cortex.py`
only. Their environments removed checkout `PYTHONPATH` and
`CORTEX_SOURCE_MODE`, set isolated `HOME`/`CODEX_HOME`, and used
`PYTHONDONTWRITEBYTECODE=1` plus `python3 -B`. The server-reported candidate
path, build identity, and source digest matched the staged package. No stable
profile or installed user plugin was touched.

The exact-candidate command was:

```text
CORTEX_PHASE_D_CANDIDATE_ROOT=<staged content-addressed candidate> \
PYTHONDONTWRITEBYTECODE=1 python3 -B -m pytest -q \
  tests/test_phase_d_decision_qualification.py --disable-warnings
```

Result: **11 passed, 0 skipped, 0 failures, 0 errors**. This includes all
clarification, plan-review (approve/request_revision/cancel), steering,
replay/conflict, stale/cross-project, restart/reconciliation, governance,
provenance, and closed-catalogue cases.

The same run includes the exit-code/stderr-aware 80 simultaneous-pair
candidate stdio stress. Each of 80 isolated projects used two independent
candidate children issuing identical opens concurrently and then identical
records. The result was **1 stress test passed**: every pair converged on one
server-owned binding, one open receipt, one decision mutation/receipt, and one
exact replay; changed input returned non-retryable `command_conflict`; no
duplicate mutation, SIGBUS, nonzero exit, forced termination, hidden EOF, or
stderr was observed. Post-run inspection found no Python-created WAL/SHM
sidecars. Generated bytecode inventory under `plugins/` and `tests/` was empty
before and after the run.

Phase D exact-candidate qualification is therefore **passed** for the decision
vertical slice. The next gate is the focused LLM-driven live-dev session; this
record does not claim that live-dev has run.

## Latest R1–R4 source re-review — 2026-08-29

R1 and R2 remain source-supported after the latest remediation: canonical error
coverage is exhaustive for literal runtime codes, and receipt contention,
exact-request conflict, and read-only reconciliation are centralized. Two P1
source blockers remain before candidate qualification can be meaningful:
`open_plan_review` and publication replay still require the persisted view's
sequence to equal the mutable global timeline maximum, and `publish_plan`
still permits a nested report/handle surface containing canonical IDs. The
exact required corrections and source-test gates are in
[phase-d-candidate-root-cause.md](phase-d-candidate-root-cause.md). Candidate
and live-dev remain unrun after this remediation.

## Post-remediation source verification — 2026-08-29

The later source-remediation claim was independently checked against the
current production files. R1/R2 source evidence remains supported. R3/R4 are
still blocked at P1: immutable plan relation replay/open is gated by mutable
global timeline maximum, and `publish_plan` still exposes a permissive nested
report/handle surface that can contain canonical IDs. The family
`decision_output` schema also still advertises `decision.decision_id`, despite
the runtime compact projection omitting it. The focused tests express the
required corrections but were not executed in this read-only review; candidate
and live-dev remain unrun.

## Final bounded source verification — 2026-08-29

The latest R3/R4 source remediation is now cleared at the source gate. Approval
relation validity uses only immutable task/project/type anchors, report/view
digests and source sequence, readiness, and the persisted opaque handle;
unrelated later timeline events do not affect open replay or record. The
focused changed-view path yields a distinct server-issued relation/binding.

All six family decision output schemas and the complete `publish_plan` output
are recursively closed and omit canonical IDs. Static catalogue equality,
schema validation, and compact-handle projection checks pass. R1/R2 remain
source-supported with no new P0/P1 findings. This does not qualify the
candidate or live-dev gates: D-ADV-013 remains open and unrun.

## Acceptance adjudication of the six candidate mismatches — 2026-08-29

This matrix adjudicates the six failures from the fresh exact-candidate run
against the current registry, advertised schemas, and runtime projections. It
does not authorize weakening assertions or treating a stale allowlist as a
second public error contract.

| Mismatch | Observed behavior | Adjudication | Minimal required change |
| --- | --- | --- | --- |
| Clarification changed-response conflict | A changed response after a committed exact record returns canonical `command_conflict`. | **Qualification expectation defect; production is correct.** `run_command_receipt` owns the occupied logical slot and rejects changed semantic input without mutation. | Replace the retired alias set in `test_clarification_is_exactly_once_localized_and_replay_safe` with the canonical registry class and assert non-retryable/no second decision. |
| Plan-review approve changed-outcome conflict | The approve case reaches first record and exact replay; a changed outcome returns canonical `command_conflict`. | **Qualification expectation defect; production is correct.** The outcome is part of the receipt request, not a family-specific conflict taxonomy. | Update the parametrized changed-outcome assertion to the advertised canonical class and preserve the no-duplicate assertion. |
| Plan-review request-revision changed-outcome conflict | The request-revision case likewise succeeds on exact replay and returns `command_conflict` for changed outcome. | **Qualification expectation defect; production is correct.** `request_revision` remains data in `record_plan_review`, not a public receipt command. | Reuse the canonical conflict assertion; do not add `record_request_revision` or an alias. |
| Plan-review cancel changed-outcome conflict | The cancel case likewise succeeds on exact replay and returns `command_conflict` for changed outcome. | **Qualification expectation defect; production is correct.** Cancellation remains bound to the same immutable relation and receipt slot. | Reuse the canonical conflict assertion and verify no replacement decision/relation. |
| Steering supersession result shape | The bounded nested `decision` object is present, exposing compact `decision_ref` and `relations.supersedes_decision_ref`; canonical `decision_id` and `supersedes_decision_id` are intentionally absent. | **Qualification expectation defect; public schema/projection are correct.** The test reads retired canonical fields despite the recursively closed advertised result. | Assert the first compact `decision_ref`, then assert the second typed supersession relation equals it; use compact refs for subsequent checks. |
| Stale/cross-project safety classes | The stale changed command is classified as canonical `command_conflict`; a binding from another project is classified as `clarification_binding_not_found`. | **Qualification expectation defect; production and registry are correct.** Both are non-retryable safe classes; `binding_not_found` and related aliases are retired. | Update the safety test's accepted sets to the exact canonical classes while retaining `isError`, bounded-detail, no-traceback/private-text, and zero-mutation checks. |

No production or public-schema defect was found in these six mismatches. The
required correction is limited to stale qualification expectations. The minimal
requalification set is the complete
`tests/test_phase_d_decision_qualification.py` command after those assertions
are aligned: it must return zero failures and zero skips, preserve exact replay
and no-duplicate checks, and exercise all six family operations. Candidate
acceptance may proceed only after that result; live-dev remains a separate gate.

## Candidate root-cause gate: pre-receipt storage admission — 2026-08-29

The corrected strict candidate run is **9 passed, 1 failed, 0 skipped**. The
remaining failure is not qualification expectation drift: one of two
independent real candidate stdio processes issuing identical
`open_clarification` calls received `storage_unavailable`.

The source review identifies this as **D-CAND-006 (P1)**. Compact task
resolution enters `V12Store.for_task_ref`, whose `_verify_known_task` performs
`BEGIN IMMEDIATE` plus migrations/backfill and schema checks before
`DecisionAggregate.open` reaches `run_command_receipt_resolved`. WAL/connection
setup and sidecar finalization are also outside the receipt executor. The
central receipt retry/reconciliation policy therefore cannot cover the
earliest contention boundary, and the current 15-second SQLite timeout is not
coherent with the executor's 0.8-second budget. See
[phase-d-candidate-root-cause.md](phase-d-candidate-root-cause.md) for the
generic admission-gate remediation and acceptance matrix.

The candidate gate remains blocked until a source fix proves one shared,
bounded admission policy for all public commands, preserves fail-closed schema
and filesystem diagnostics, and demonstrates exact two-process convergence in
the complete real-stdio candidate suite. No operation-specific sleep or
qualification allowlist is an acceptable fix; live-dev remains prohibited.

## D-CAND-006 remediation re-review — 2026-08-29

The shared-admission source change is **not cleared**. The task-shard path is
now wrapped by `_with_storage_admission`, and `_connection` derives its SQLite
timeout from a deadline. However, compact record resolution still begins with
the direct `_record_locator_matches` connection, and the legacy fallback can
call `_sync_record_locators` outside that gate. `_record_in_task` can likewise
call `resolve_record_ref` after `_task_store` has restored its deadline. These
are public pre-receipt paths, so a first call can still fail before the central
receipt executor.

The deadline is not end-to-end: `_task_store` resets `_contention_deadline`
before domain/receipt work, `_write_once` sleeps without clamping to remaining
time, and journal setup classifies busy by exception text rather than the
canonical SQLite code. Automatic forward migration and fail-closed schema
validation remain present, while sidecar cleanup no longer masks an exception
from the yielded operation; neither fact proves complete admission coverage.

The focused synthetic test only proves one retry and primary-error
preservation. It does not prove independent-process public convergence through
task/record resolution, locator repair, migration, WAL setup, and receipt
entry. D-CAND-006 remains P1-blocked until those deterministic source tests
and the complete exact-candidate stdio suite pass with zero failures/skips; no
candidate/live-dev execution was performed in this review.

## D-CAND-006 final bounded source re-review — 2026-08-29

Source clearance remains **blocked (P1)**. The remediation covers task-shard
lookup, bootstrap, known-anchor verification, reads, writes, WAL/pragmas, and
receipt entry with a shared helper and a derived SQLite timeout. It does not
cover the direct `record-locators.db` lookup before `for_record_ref` admission,
legacy locator repair/post-commit locator refresh, or every
`resolve_record_ref` call after `_task_store` restores its deadline.

The public command also lacks one end-to-end deadline: `_write_once` can sleep
past the remaining budget, and journal setup matches the SQLite error string
instead of using the canonical busy/locked code. Automatic forward migration,
fail-closed schema checks, and primary-error preservation for yielded
connection failures are retained, but they do not close these bypasses.

The only new focused test is synthetic (`test_shared_admission_budget...`): it
does not prove two independent stdio processes converge through task/record
resolution, locator repair, migration, WAL setup, and receipt admission. No
candidate/live-dev run was performed. D-CAND-006 can be cleared only after the
generic gate covers every public storage path and the complete exact-candidate
suite passes with zero failures and zero skips.

## D-CAND-006 ContextVar remediation re-review — 2026-08-29

The current source propagates one `_ADMISSION_DEADLINE` through task and record
locator resolution, schema readiness/migration/backfill, WAL/pragmas, anchor
verification, reads/writes, and receipt transaction entry. SQLite busy
classification is code-based, waits are capped to the remaining deadline, and
primary yielded-operation errors are preserved from sidecar cleanup. Automatic
forward migration and fail-closed schema behavior remain present. No source
P0/P1 implementation defect was found in these paths.

The focused source tests are still not a real two-process stdio integration:
they verify ContextVar inheritance and synthetic contention, while direct
receipt tests verify replay/concurrency. The existing exact-candidate test is
the correct black-box qualification for this invariant because it launches two
independent candidate `cortex.py` stdio processes and uses the actual MCP
handler. It is sufficient for the candidate gate once green, but it cannot be
used as source-clearance evidence under the repository's separate source and
candidate gates. A bounded source-mode two-process stdio regression remains
required before D-CAND-006 source clearance; it must preserve the same
one-binding/one-receipt invariant and never weaken the candidate assertion.

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

## D-CAND-006 source boundary closure — 2026-08-29

`record-locators.db` is now explicitly modeled as a reconstructible,
non-authoritative accelerator. A post-commit refresh failure is swallowed only
after canonical receipt/mutation success; normal compact resolution verifies
the ledger and uses bounded legacy recovery/repair. The inherited admission
context therefore remains active through the refresh attempt without allowing a
derived index failure to revoke the successful public result. A bounded source
test starts two independent `cortex.py` MCP processes, performs full
initialization and identical `open_clarification`, and observes two successful
results with one binding. This is source-only evidence and does not replace the
exact-candidate or live-dev gates.

## D-CAND-006 read-only source re-review after host restart — 2026-08-29

The bounded source checks passed with bytecode disabled (`29 passed, 16
subtests passed`). The result is **not source clearance**. `record-locators.db`
is documented as reconstructible, but the current fallback path still lets a
repair exception prevent return of an already verified canonical record, and
the indexed path treats sidecar schema/read failures as canonical
`storage_unavailable` rather than falling back. Bootstrap can fail for the
same derived-index reason after canonical schema commit. The source stdio
regression does not yet assert one `command_receipts` row, changed-input
`command_conflict`, or locator corruption/fallback. Candidate qualification
and live-dev remain unrun and blocked pending those source assertions and the
generic sidecar-authority correction.

## D-CAND-006 independent post-remediation race review — 2026-08-29

The current source regressions pass and the strengthened source MCP scenario
now checks same binding, exactly one binding, one open receipt, one record
mutation/receipt, and changed-input `command_conflict`. Direct locator
injections also pass, including malformed-sidecar fallback, bootstrap repair
failure, and canonical schema-failure preservation.

The source gate is still **P1-blocked**. An independent repeated two-process
admission run intermittently returned `storage_unavailable` from
`_materialize_sidecars` when SQLite removed `cortex.db-wal` between its
owner-only validation and `os.chmod()`. This canonical WAL/SHM race is outside
the effective busy retry boundary. Candidate qualification and live-dev must
not run until the generic admission implementation handles this race within
the inherited deadline without weakening fail-closed canonical validation.

## D-CAND-006 locator-authority source evidence — 2026-08-29

The source boundary is now covered by deterministic failure injection. A
missing, malformed, unreadable, or schema-incompatible `record-locators.db`
does not become a canonical storage failure: resolution falls back to exact
canonical shard verification and attempts bounded idempotent repair. If repair
cannot complete, the verified record remains the result. Bootstrap likewise
keeps a canonically ready database usable when its initial derived sidecar
rebuild fails.

The source-mode two-process MCP regression asserts two successful identical
opens, one binding, and one matching open receipt. It then records one response
and proves that a changed response returns canonical `command_conflict`, with
no duplicate decision or record receipt. This is not a candidate pass: the
exact content-addressed candidate matrix and LLM-driven live-dev scenario
remain required and unrun.

## D-CAND-006 final WAL/SHM stress adjudication — 2026-08-29

The deterministic sidecar injections and successful source MCP assertions pass,
but source qualification remains **P1-blocked**. A 20-run repetition of the
four-round two-process stress (80 races) failed when one source child closed
stdout before its JSON-RPC response while the peer returned a replayed result;
an independent 80-race harness observed a child exit `-7` (SIGBUS). This is a
real hidden-response/process-crash race in SQLite WAL/SHM admission. Candidate
qualification and live-dev must remain unrun until the generic lock/sidecar
boundary is corrected and bounded stress has zero errors, crashes, hangs,
split bindings, or leaked children.

## D-CAND-006 final independent source adjudication — 2026-08-29

The current locator-authority tests pass, and the source MCP scenario checks
same binding, one binding, one open receipt, one record mutation/receipt, and
changed-input `command_conflict`. Nevertheless, source qualification remains
**P1-blocked**: repeated independent process admission intermittently returns
`storage_unavailable` from `_materialize_sidecars` after SQLite removes
`cortex.db-wal` between the sidecar check and `os.chmod()`. This canonical
WAL/SHM race is outside effective busy retry handling. Candidate qualification
and live-dev must remain unrun until the generic bounded admission policy
absorbs this race while retaining fail-closed canonical validation.

## D-CAND-006 WAL/SHM source qualification — 2026-08-29

Source qualification now includes a deterministic WAL disappearance injection:
the test removes an already validated regular WAL just before descriptor
`fchmod`, verifies no storage failure escapes, and verifies canonical read
availability afterward. It also includes four bounded two-process source
`cortex.py` MCP rounds. Every round requires two successful identical opens,
one returned binding capability, one canonical clarification binding, and one
matching `open_clarification` receipt; no replay creates a second mutation.

The admission implementation is centralized rather than operation-specific:
SQLite owns WAL/SHM creation/removal, while a descriptor-validated per-shard
lock serializes only connection/WAL admission under the existing inherited
deadline. The source response helper records any empty/invalid JSON-RPC
response as a visible test failure and always closes stdin before terminating
only its exact child; the final green stress left no child or hidden diagnostic.
Candidate and LLM-driven live qualification remain mandatory but unrun.

## SQLite sidecar final source qualification — 2026-08-29

Qualification now requires every source MCP child EOF to carry exit status and
bounded stderr classification. The 80-pair run had zero nonzero exits, forced
termination, hidden responses, tool errors, duplicate bindings, or duplicate
receipts. Reentrancy, error release, process-exit recovery, and unrelated
shard independence are directly covered. Candidate and live qualification
remain separate and unrun.

## D-CAND-006 final independent WAL/SHM stress adjudication — 2026-08-29

The deterministic sidecar injections and successful source MCP assertions pass,
but source qualification remains **P1-blocked**. A 20-run repetition of the
four-round two-process stress (80 races) failed when one source child closed
stdout before its JSON-RPC response while the peer returned a replayed result;
an independent 80-race harness observed a child exit `-7` (SIGBUS). This is a
real hidden-response/process-crash race in SQLite WAL/SHM admission. Candidate
qualification and live-dev must remain unrun until the generic lock/sidecar
boundary is corrected and bounded stress has zero errors, crashes, hangs,
split bindings, or leaked children.
## D-CAND-006 final connection-lifetime lease review — 2026-08-29

Current source qualification evidence is green for active behavior.  The
lease is per-shard, descriptor-validated, PID-aware, reentrant, acquired
before connect, held through close, and bounded by one inherited deadline.
The source MCP 80-pair stress and focused source suite passed; locator
fallback/repair, canonical fail-closed behavior, exact binding convergence,
single receipts/mutation, and changed-input conflict also passed.

Qualification remains **P1-blocked** by a source-level invariant violation:
`_secure_sqlite_sidecar` still reaches `os.fchmod` for a WAL/SHM path.  The
active path does not call it, but qualification requires that Cortex never
mutate live SQLite sidecars anywhere.  Remove or neutralize this helper and
add a static/runtime no-mutation assertion before source clearance.  Exact
candidate and LLM-driven live-dev gates remain unrun.

## Latent-path source qualification — 2026-08-29

Static AST conformance now proves all production WAL/SHM handling is
validation-only, including latent helpers. The 80-pair exit-aware source-MCP
stress is green; exact candidate and live remain unrun.
## D-CAND-006 final post-removal clearance check — 2026-08-29

The latent sidecar helper has been removed; static inventory and runtime
stress show validation-only WAL/SHM handling, separate canonical protection,
and green 80-pair source-MCP behavior.  Qualification remains **P1-blocked**
because `test_production_wal_shm_paths_are_validation_only` is lexical and
can be bypassed without changing the tested behavior (for example, an alias
or computed suffix).  Replace it with a non-bypassable call/path boundary
assertion before source clearance.  Candidate and LLM-driven live-dev gates
remain unrun.

Conformance tests reject computed suffix, alias import, helper-indirection,
pathlib, and getattr mutation bypasses. Candidate/live remain unrun.

## Capability-boundary source qualification — 2026-08-29

The lexical-guard P1 qualification finding is resolved at source scope. A
central registry checks the complete runtime package and launcher, and its
negative fixtures prove rejection of computed suffix, alias-import,
helper-indirection, pathlib, and `getattr` bypasses. The registry has no
live-sidecar mutation capability; canonical database protection and offline
validated backup retention are separately named purposes.

The 80-pair real source-MCP test executes a per-child Python mutation observer
and requires no WAL/SHM mutations, clean child exits, one binding, and one
receipt per pair. This is source qualification only. Exact candidate and
LLM-driven live-dev remain unrun and therefore unqualified.

## Recursive P1-bypass qualification — 2026-08-29

Source qualification includes negative fixtures for assignment aliases,
`import pathlib as` aliases, from-import OS/shutil aliases, aliased helper
calls, dynamic lookup, nested modules, and global/default/closure storage.
The real 80-pair source-MCP stress is accompanied by an observer self-tested
for FD-based `write`, `pwrite`, `ftruncate`, and `truncate` handling. It still
does not intercept SQLite's internal C calls. Candidate and live-dev remain
unrun.
## D-CAND-006 final filesystem-boundary review — 2026-08-29

The helper-removal, validation-only sidecar path, active child observer, and
80-pair source-MCP stress checks pass.  The package policy rejects the
current direct/aliased/pathlib/dynamic/helper fixtures and the source/domain
focused suites are green.

Qualification remains **P1-blocked** because additional read-only adversarial
fixtures demonstrate accepted assignment aliases and aliased pathlib modules;
the scanner is non-recursive, and the observer likewise does not cover every
low-level write/truncate API.
The no-mutation boundary must be strengthened against those bypasses before
source clearance.  Candidate and LLM-driven live-dev gates remain unrun.
## D-CAND-006 final filesystem-policy adversarial review — 2026-08-29

The recursive policy, active child observer, focused conformance/domain
checks, and 80-pair source-MCP stress pass.  Qualification remains
**P1-blocked** because subscript call targets and helper-returned/callback
mutator storage still have practical bypasses.  Add negative fixtures and
reject those callable-flow forms before source clearance.  Candidate and
LLM-driven live-dev gates remain unrun.

Final P1 fixtures prove subscript-call rejection and callable-flow rejection:
returns, yields, list/dict/object storage, `partial`, callback passing, and
nested-helper export/call. The 80-pair source stress remains exit-aware and
requires a clean observer result.

Additional negative fixtures cover helper-returned `Path`, an assigned
returned constructor, one- and two-hop return chains, and returned `pathlib`,
`os`, and `shutil` modules before an indirect mutating call.
## D-CAND-006 final callable-flow review — 2026-08-29

The focused guard/stress gate passes, including the 80-pair source-MCP run
and expanded callable/FD fixtures.  Qualification remains **P1-blocked**
because helper-returned pathlib constructors can still reach mutation through
an indirect call.  Close that constructor-flow gap before source clearance;
candidate and LLM-driven live-dev gates remain unrun.
## D-CAND-006 final declared-scope clearance — 2026-08-29

The final callable-flow audit is green: returned constructors/modules for
`pathlib.Path`, `pathlib`, `os`, and `shutil` are rejected through direct,
assigned, one-hop, and two-hop helper use.  The focused policy/domain run
passed (`3 passed, 106 subtests`), including the exit-aware 80-pair source
MCP stress and isolated `CODEX_HOME` execution.

Source qualification is **cleared within the declared Python/runtime scope**.
Exact candidate and LLM-driven live-dev gates remain unrun and unqualified.

## Packaging closure blocker — 2026-08-29

The exact-candidate gate found that `filesystem_policy.py` was present in the
checkout but absent from the authoritative staged Python payload. This was a
delivery defect, not a Decision API behavior result: a candidate could pass
catalogue/provenance checks while omitting a production source module used by
the filesystem-boundary qualification.

The correction is architectural. `plugins/cortex/runtime-payload.json` is now
the one canonical installable Python closure. The candidate builder and
marketplace validator both derive the expected launcher-plus-runtime-module
set from the tree and require byte-for-byte agreement with that manifest.
`filesystem_policy.py` is declared, staged, importable from the isolated
candidate, and included in the content-addressed digest. An unlisted runtime
module fails the release-candidate gate. This packaging correction does not
promote any Decision row: the complete exact-candidate matrix and live-dev
gates remain separate and must still pass with zero failures and zero skips.

The closure gate is implemented once in `scripts/cortex_payload_manifest.py`
and consumed by both packaging paths. It recursively checks nested runtime
packages and exact directory topology, including rejection of undeclared empty
directories and candidate/plugin-root symlinks. The candidate catalogue count
is derived from the semantic registry; packaging tests contain no fixed
eleven/fifteen-tool assumption. These are source/package gates only and do not
replace the required full exact-candidate Decision run or live-dev run.

The earlier source marketplace failures reporting `storage_unavailable` were
test-harness contamination: compact-reference resolution correctly reads
`CODEX_HOME`, while the tests had inherited the user's stable state root.
`MarketplaceReleaseGate` now creates and restores an isolated temporary
`CODEX_HOME`/`HOME` per test. With that isolation, the storage failures no
longer reproduce; two remaining failures are stale assertions for intentionally
closed public projections and are not packaging evidence or runtime changes.

## Marketplace stale-projection assertion adjudication — 2026-08-29

The two previously remaining `tests/test_marketplace_release_gate.py` failures
were qualification-test drift, not packaging failures. The public projection is
intentionally closed and exposes compact typed references; canonical durable
`decision_id` and `report_id` fields are internal and must not be reconstructed
or made callable. The exact test corrections are:

```text
decision["decision"]["decision_id"]
    -> decision["decision"]["decision_ref"]

record_ref(first["report"]["report_id"])
    -> first["report"]["report_ref"]
```

The second correction passes the already-issued compact report reference
directly to the verifier relation; wrapping it as a canonical record ID is
also invalid. The Decision qualification owner has since applied these
assertion corrections, and the complete marketplace gate is green. This
review did not edit the tests; the packaging-specific provenance,
candidate-tree, and marketplace-structure checks remain independently green.

## Trusted ancestry qualification — 2026-08-29 re-review

The shared lstat ancestor-chain helper and direct candidate checks are present:
candidate staging, candidate roots, plugin roots, nested runtime entries, and
marketplace plugin topology are covered, and extra empty directories fail in
the direct release-candidate path. However, packaging is **not source-cleared**
yet. `/home/igovet/Web_Projects/codex-orchestration/scripts/sync-cortex.sh`
still resolves the installed cache path before its content comparison, so a
symlinked cache version parent can bypass the intended ancestry check. The
sync reuse path must validate the lexical version/cache ancestors before any
resolution. The two stale assertions in
`tests/test_marketplace_release_gate.py` also remain unedited in this
read-only review; the exact required corrections are the compact
`decision_ref` and `report_ref` replacements documented above. The focused
packaging subset is green, but the combined marketplace file remains red and
the full Decision candidate/live-dev gates remain separate and unrun.

## Final packaging clearance — 2026-08-29

The sync-path remediation is now verified. `scripts/sync-cortex.sh` validates
the lexical repository, `.cortex-candidates` staging root, installed version
root, and all managed ancestors with `lstat` before reading, reusing, or
comparing a candidate. Real isolated sync tests reject symlinked staging and
installed-version parents, while the normal two-run reuse test proves that an
unchanged content-addressed candidate is not installed twice. Recursive
runtime closure, exact plugin topology, root safety, digest determinism, and
filesystem-policy staging/importability also pass. The focused packaging and
marketplace suites are green with zero failures and zero skips; packaging is
cleared within the managed path/package-invariant scope. The exact Decision
candidate and LLM-driven live-dev gates remain separate and unrun.

## Current qualification status — 2026-08-29 (supersedes earlier entries)

The final exact-candidate decision gate is now **passed**. The fresh isolated
sync produced `1.12.1+codex.sha256.eb691a9a49377dcc` with source/candidate
digest `eb691a9a49377dcc24640d415c5fa38d8e94b7cb33c104277443c5c3004c453f`.
Marketplace and release validation passed (`files=94`), and the candidate MCP
initialization reported `parityVerified=true`. Candidate children removed
checkout `PYTHONPATH` and `CORTEX_SOURCE_MODE`, used isolated `HOME` and
`CODEX_HOME`, and ran with `PYTHONDONTWRITEBYTECODE=1`/`python3 -B`.

The full `tests/test_phase_d_decision_qualification.py` run against that staged
candidate passed **11 tests, 0 failures, 0 skips, and 0 errors**. Its 80-pair
candidate stdio stress passed with two independent children per pair: one
binding/open receipt, one record mutation/receipt, exact replay, and a
changed-input `command_conflict` for every pair. There were no duplicate
mutations, SIGBUS/nonzero exits, forced terminations, hidden EOFs, stderr, or
observed Python-side WAL/SHM sidecars. Bytecode inventory under `plugins/` and
`tests/` was empty before and after qualification.

The exact-candidate gate is complete for this vertical slice. The next gate is
the focused LLM-driven live-dev session; live-dev has not been run here.

## Focused live-dev gate — 2026-08-29

The first post-reboot operator-controlled attempt is recorded in
[phase-d-live-verification.md](phase-d-live-verification.md). It failed before
the ordinary Codex composer appeared because the isolated profile's existing
`cortex` marketplace registration was rejected as being from a different
source. No prompt or MCP call was made, and no live pass is claimed. The
isolated refresh/marketplace lifecycle is now source-remediated with native
same/missing/different-source convergence and isolated-target refusal tests;
the focused ordinary-Codex retry remains required.

## Operator transport precondition — 2026-08-29

The focused retry now has a separate, source-tested observation layer. Before
the ordinary launcher is released, `cortex-live-smoke` attaches an owner-only,
bounded, output-only `pipe-pane` stream to the exact named pane. `capture`
exposes that bounded stream to the LLM when detached pane capture misses the
alternate screen. The driver does not parse trust, composer state, tool calls,
or acceptance; it therefore adds no candidate or live pass by itself.

When the LLM/operator visibly observes the fresh-project acknowledgement, the
new `enter` action may deliver exactly one standalone key to that exact pane.
It does not trust a path, write trust configuration, deliver a workload, or
approve/answer any product decision. The composer must be visibly observed
again before the existing task-specific prompt transport is used. Stop closes
the output pipe and deletes the private bounded stream before removing only the
named session. This correction was verified without starting live-dev; the
focused decision and worker-event acceptance remains unrun.
# Phase D qualification addendum — hidden worker outcome observation

The focused candidate and live qualification matrix includes the owner-only
sanitized MCP event journal. Source tests prove that the public stdio boundary
records every terminal tool success/error with semantic operation, safe
internal outcome/fault, command replay/conflict classification, safe anchor
fingerprints, publication metadata, and build identity without retaining raw
caller values. Malformed tool-call envelopes are also recorded once without
retaining their arbitrary input. A physical JSONL wire-size fallback is
recorded as the client-visible internal failure, never as a semantic success.
The exact session live transport exposes the bounded stream unchanged; the LLM
verifier, not the transport, evaluates it for hidden errors/replays and the
first worker-owned publication.

# Installed-candidate gate update — 2026-08-29

The machine-readable isolated receipt is valid for base version `1.12.1` and
reports matching source/candidate digest
`sha256:39bfb3402ab88178c5aeab22f66d97bddc68927f06e294f714c18f4e3e672999`.
However, the qualification fixture cannot yet consume that installed
plugin-only candidate: it computes the full checkout manifest and fails on the
absent `.agents/plugins/marketplace.json` before starting an MCP process. The
strict installed-candidate run therefore has 11 fixture-setup errors and zero
executed cases. A separate fresh staged-candidate run passes 11/11, but does
not promote any installed-cache row. Details are in
[phase-d-candidate-qualification-result.md](phase-d-candidate-qualification-result.md).

## Canonical candidate-location invariant — 2026-08-29

The qualification fixture resolves one typed location before it starts a
candidate process. A release/check-out root and a receipt-selected installed
plugin root are different topologies: the former maps once to its plugin child,
while the latter is already the plugin root. Provenance, concurrent-open, and
80-pair stress branches all consume the same resolved server/runtime paths.
They cannot append `plugins/cortex`, scan a cache, or fall back to source. A
wrong, nested, missing, symlinked, or receipt-mismatched location fails before
MCP dispatch.

The fresh installed-cache rerun reached the real stdio handler and produced
8 passes and 3 harness failures. Provenance and child-process server paths
still append `plugins/cortex` to an installed root that is already the plugin
root; the 80-pair stress therefore did not start. Phase D remains blocked and
no candidate row is promoted.

## V19 exact-session observation qualification — 2026-08-29

The isolated candidate's visible delivery receipt was coherent in a real
ordinary-Codex tmux launch, but the exact-session observer could not open its
runtime-owned nonce-bound generation before any task was submitted. The live
qualification gate therefore remains **failed/unverified**. Source/candidate
provenance alone is insufficient: promotion requires a readable matching
`server_ready` registration and then the full LLM-driven orchestration route.

The subsequent focused retry observed the required matching registration once,
then lost exact-session event readability after the single permitted workload
delivery. Qualification remains **failed**: observation availability must be
stable for the lifetime of the owned live session, including hidden worker
events, not merely at server startup.

The no-bytecode retry demonstrated stable repeated startup observation but
failed the independent first-action route gate: visible local exploration
occurred before any `open_task` event. Phase D remains **failed** until the
ordinary live coordinator starts the Cortex route with exactly one task opening
and proceeds through observable worker stages.

The corrected-bootstrap retry removed the false skill-loading failure mode but
still reached no task opening within the bounded live turn; the exact event
stream remained registration-only. Phase D remains **failed** at the required
first semantic task-operation boundary.
