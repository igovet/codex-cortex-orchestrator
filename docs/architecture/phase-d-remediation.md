# Phase D decision remediation matrix

Status: source remediation for Cortex `1.12.1`. This document records the
production ownership and source-focused regression evidence for the adversarial
findings. It is not candidate qualification or live-dev evidence.

## Architectural result

The public Decision boundary is three family pairs: clarification, plan review,
and steering. Each `open_*` projects one exact scalar `binding_ref` into the
family result and its `handles` object; only the matching `record_*` operation
accepts it. The adapter resolves compact public locators once through the store
before passing canonical durable IDs to the aggregate. Canonical IDs remain
storage evidence and are never reconstructed by callers.

`DecisionAggregate` derives an open command's effective-contract revision,
receipt slot, request digest, and binding inside the same `BEGIN IMMEDIATE`
transaction. Record-command identity includes every family semantic input;
for steering that includes the complete closed delta and an optional typed
same-task supersession relation. The backend still only verifies and persists
the coordinator's intent: it does not schedule work, author questions, or
approve a plan.

Receipt provenance is also family-owned: one authoritative family map supplies
the exact public `open_*` and `record_*` command names, rather than deriving a
name from an internal type or a plan-review outcome. A plan-review opening
resolves its finalized plan and ready approval view in that same write
transaction, then persists the plan digest, approval handle, view digest, and
view source sequence on its binding. Every plan-review outcome is intended to
read and validate only those persisted values; the bounded adversarial review
below records the remaining replay/open defect where a mutable global timeline
check can nevertheless invalidate an otherwise valid relation.

## Remediation matrix

| Finding | Root remediation | Production owner | Source regression evidence | Status |
| --- | --- | --- | --- | --- |
| D-ADV-001 | Family adapter projects the exact binding scalar at top level; MCP handle projection copies it verbatim. | `domain_api.py`, `mcp_api.py` | `test_public_mcp_first_call_conformance` composes each public family open/record pair. | source-tested |
| D-ADV-002 | `V12Store.resolve_task_reference` is the single compact-reference resolver; it verifies type, task, and project before the aggregate receives a canonical ID. | `v12_store.py`, `domain_api.py` | Decision family composition and store resolver tests. | source-tested |
| D-ADV-003 | The semantic registry and public test surface use only the six typed family operations; no overloaded decision operation is public. | `semantic_registry.py`, `domain_api.py`, public MCP tests | Registry/catalogue tests. | source-tested |
| D-ADV-004 | Receipt request normalization includes steering delta and superseded canonical decision ID. | `domain_kernel.py` | Aggregate steering replay/conflict tests. | source-tested |
| D-ADV-005 | Closed steering schema advertises `supersedes_decision_ref`; adapter resolves it to a same-task decision and store persists it atomically. | `public_contracts.py`, `domain_api.py`, `v12_store.py` | Steering supersession regression. | source-tested |
| D-ADV-006 | A resolved receipt API derives revision, slot, and binding after `BEGIN IMMEDIATE` acquires the lock. | `v12_store.py`, `domain_kernel.py` | Concurrent identical-open regression. | source-tested |
| D-ADV-007 | `steering_delta` is recursively closed with concrete additions and at least one operation. | `public_contracts.py` | Contract schema regression. | source-tested |
| D-ADV-008 | Runtime catalogue language derives its count from `OPERATION_NAMES`. | `cortex.py`, `public_contracts.py` | Registry/catalogue order test. | source-tested |
| D-ADV-009 | Retired decision aliases are private historical helpers and absent from `domain_api.__all__`. | `domain_api.py` | Public-export regression. | source-tested |
| D-ADV-010 | `tools/list` emits each contract's actual family-specific `outputSchema`. | `mcp_api.py` | Real stdio catalogue equality regression. | source-tested |
| D-ADV-011 | A centralized DecisionAggregate family map persists the exact registry/public operation name for every family open/record receipt. Plan-review outcomes remain data in a `record_plan_review` receipt. | `domain_kernel.py` | `test_receipts_use_public_semantic_family_names` checks steering and all three plan-review outcomes. | source-tested |
| D-ADV-012 | Forward-only v17 binding columns atomically capture the immutable plan digest and exact approval-view relation. Record paths validate the captured relation and never select a current/newest projection or handle. | `v12_store.py`, `domain_kernel.py`, `domain_api.py`, `public_contracts.py` | `test_plan_review_binding_survives_newer_view_concurrency_and_restart` creates a competing newer view/handle, concurrently records, then replays through a new aggregate. | source-tested |
| D-ADV-014 | The authoritative maintenance required-column contract now includes all four v17 immutable plan-review relation fields and rejects nominal v17 shards missing any one field without repair or downgrade. | `v12_maintenance.py` | `test_v17_maintenance_schema` proves a healthy v17 pass and reports/fails closed for each missing relation column. | source-tested |
| D-CAND-001, D-CAND-005 | Registry-owned `ErrorSpec` contracts now cover every intentionally typed public store/domain code, while unknown faults alone project as `ledger_error`. Operation metadata contains canonical codes only. | `semantic_registry.py`, `domain_kernel.py`, `mcp_api.py` | `test_every_declared_or_raised_public_code_has_one_canonical_spec` scans declared/raised public codes and verifies one registry owner; unknown-fault projection remains sanitized. | source-tested |
| D-CAND-002 | The shared write/command-receipt executor has bounded SQLite busy acquisition and exact-slot read-only reconciliation after contention; it never creates a replacement binding or reruns a reconciled mutation. | `v12_store.py` | Cross-process one-mutation test plus `test_forced_busy_lost_response_restart_reconciles_without_mutation` prove busy, restart, lost-response replay, and changed-digest conflict. | source-tested |
| D-CAND-003 | Semantic plan publication atomically renders the immutable revision, persists ready projection metadata/digest, mints the exact approval handle/relation, finalizes the report operation, and returns the relation together. Immutable relation validity is independent of later unrelated task chronology. | `v12_store.py`, `v12_service.py`, `domain_api.py` | Lifecycle/rollback evidence plus later unrelated governance/initiative events before open replay and record proves stable relation use; a changed view yields a distinct relation/binding. | source-tested |
| D-CAND-004 | Family decision projection exposes only bounded compact decision/subject refs and a recursively closed steering supersession relation. `publish_plan` emits and advertises a recursively closed compact report/digest/approval relation and typed closed handles, without canonical internal identifiers. | `domain_api.py`, `mcp_api.py`, `public_contracts.py` | Real tools/list/runtime first-call conformance proves schema equality, closed structure, and no canonical decision/task/subject/report/delegation/operation IDs. | source-tested |
| D-CAND-006 | One generic storage-admission budget covers pre-receipt shard/reference readiness, WAL/pragmas, schema migration/backfill, locator sidecar repair, and receipt `BEGIN IMMEDIATE`; cleanup cannot mask a primary typed storage error. `record-locators.db` is explicitly reconstructible/non-authoritative, so post-commit refresh failure preserves the committed canonical result and later resolution repairs/falls back safely. | `v12_store.py` | Partial source evidence: failure injection proves post-commit refresh cannot revoke canonical task creation; two independent `cortex.py` source MCP processes converge on one clarification binding. Locator fallback/repair and receipt/conflict assertions are not yet covered. | P1 source-clearance blocked; candidate gap |

## Evidence boundary

The remediation does not alter the installed plugin profile, invoke an
installation or synchronization command, or run live-dev. Candidate and live
acceptance remain governed by
[Phase D decision qualification](phase-d-qualification.md) and the
[decision capability parity matrix](decision-capability-parity-matrix.md).

## Latest adversarial source re-review — 2026-08-29

The remediation claims above are not candidate or live evidence and are
overridden by the bounded source review recorded in the linked root-cause and
adversarial documents. R1/R2 remain source-supported; R3/R4 retain the P1
defects shown in the matrix: mutable global latest-timeline gating on relation
replay/open, and canonical-ID exposure through the family/publication schemas.
No production or test files were changed by that review, and candidate/live-dev
were not run. D-ADV-013 remains an open candidate/live gate.

## Final bounded source re-review — 2026-08-29

The newest R3/R4 remediation is source-cleared. Approval relation validity no
longer depends on global/current timeline state; unrelated chronology preserves
open replay and record, while a materially changed rendered view receives a
distinct server-issued relation/binding. `publish_plan` and all six family
decision results are recursively closed compact projections, with typed refs,
digests, sequence values, and opaque handles only. Centralized handle
projection and tools/list/runtime schema equality checks pass. R1/R2 remain
source-supported, with no new P0/P1 source finding. Candidate qualification and
LLM-driven live-dev remain separate mandatory gates and are still unrun.

## D-CAND-006 source re-review after host restart — 2026-08-29

The focused source set passed (`29 passed, 16 subtests passed`) with bytecode
generation disabled. This does not grant D-CAND-006 clearance. The current
source still has two non-authoritative-locator violations: fallback record
resolution synchronously requires a successful sidecar repair after the
canonical shard row is found, and invalid/unreadable sidecar schema is
returned as `storage_unavailable` instead of triggering canonical fallback.
Initial bootstrap can likewise fail after canonical schema commit when the
derived sidecar rebuild fails. The source two-process MCP test checks
initialization, compact task resolution, shard readiness, identical open
success, and one binding row, but not the receipt row, changed-input conflict,
or locator fallback/repair. D-CAND-006 therefore remains P1/source-blocked;
candidate/live status is unchanged.

## D-CAND-006 independent post-remediation race review — 2026-08-29

The explicit locator-authority regressions now pass: fallback repair failure is
non-authoritative, malformed locator bytes fall back and rebuild, bootstrap
remains usable when derived rebuild fails, and canonical schema failures remain
fail-closed. The strengthened source MCP test asserts identical binding, one
binding row, one open receipt, one record mutation/receipt, and changed-input
`command_conflict`.

Source clearance remains **P1-blocked** because an independent repeated
two-process race still intermittently returns `storage_unavailable` during
`for_task_ref` admission. The failing path is `_materialize_sidecars`: after
SQLite unlinks `cortex.db-wal` as a connection closes, the subsequent
`os.chmod()` sees `FileNotFoundError` and projects a storage failure. This is
canonical WAL/SHM housekeeping, not the reconstructible record locator, and it
escapes the shared busy retry boundary. The fix must make SQLite-managed
sidecar validation/recreation race-safe within the inherited deadline while
keeping unsafe path and canonical schema failures fail-closed. Candidate and
live status remain unchanged.

## D-CAND-006 locator-authority remediation — 2026-08-29

The locator sidecar now has one centralized non-authoritative policy. A
canonical shard is verified before it is authoritative; failure to read,
validate, rebuild, or refresh `record-locators.db` cannot overturn that
verified result. A malformed derived image is atomically rebuilt from current
canonical shard rows under the inherited admission budget. Bootstrap invokes
the same best-effort repair only after canonical schema readiness, keeping a
ready canonical database usable if initial sidecar rebuild fails.

Focused source tests cover fallback repair failure, malformed-sidecar fallback
followed by restart resolution from the repaired accelerator, bootstrap repair
failure with canonical task resolution, and real two-process source MCP
admission. The MCP test now proves one binding, one matching open receipt, and
changed-response `command_conflict` without a duplicate decision or record
receipt. Candidate and live-dev verification are deliberately not claimed.

## D-CAND-006 final independent WAL/SHM stress adjudication — 2026-08-29

The locator-authority injections and successful source MCP assertions pass,
but source clearance remains **P1-blocked**. Repeating the four-round
two-process stress 20 times (80 independent races) produced a child that
closed stdout before its JSON-RPC response; the peer observed only a replayed
success. An independent 80-race harness also observed a source child exit
`-7` (SIGBUS). The descriptor lock and ephemeral-sidecar policy therefore do
not yet establish crash-free process-independent admission. No candidate or
live-dev result is promoted.

## D-CAND-006 final WAL/SHM stress adjudication — 2026-08-29

The locator-authority injections and successful source MCP assertions pass,
but source clearance remains **P1-blocked**. Repeating the built-in four-round
two-process stress 20 times (80 independent races) produced a child that
closed stdout before its JSON-RPC response; the peer observed only a replayed
success. An independent 80-race harness also observed a source child exit
`-7` (SIGBUS). The descriptor lock and ephemeral-sidecar policy therefore do
not yet establish crash-free process-independent admission. No candidate or
live-dev result is promoted.

## D-CAND-006 final independent source adjudication — 2026-08-29

The locator-authority remediation and focused injections pass, but the source
gate remains **P1-blocked**. A repeated independent two-process source
admission run reproduces `storage_unavailable` in `_materialize_sidecars`
when SQLite unlinks `cortex.db-wal` between `_regular()` validation and
`os.chmod()`. This canonical WAL/SHM housekeeping race escapes the shared busy
retry boundary and can make an otherwise valid first command fail. The next
root correction must make SQLite-managed sidecar housekeeping race-safe within
the inherited deadline without weakening fail-closed canonical path/schema
validation. Candidate/live status is unchanged.

## D-CAND-006 WAL/SHM admission-race remediation — 2026-08-29

The common storage layer no longer creates `cortex.db-wal` or
`cortex.db-shm`; SQLite exclusively owns their lifecycle. `_materialize_sidecars`
is now a bounded safety sweep of an extant sidecar, not a creation step. Its
descriptor-based regular-file normalization revalidates a disappearance around
`fchmod` and leaves an absent ephemeral file absent rather than reporting
`storage_unavailable`.

One descriptor-validated per-shard lock centralizes only SQLite admission
(connect, WAL mode, synchronous setup, and sidecar safety). It shares the
existing inherited monotonic deadline and capped backoff, then releases before
normal transactions. This prevents competing Cortex processes from performing
the WAL transition concurrently without adding any tool-specific retry or
changing command identity. Unsafe sidecars and canonical database/schema
faults remain fail-closed.

The deterministic deletion injection and four-round independent source-MCP
stress now pass. The latter has two simultaneous identical
`open_clarification` calls per round and proves same binding plus exactly one
canonical binding and open receipt. Candidate and live-dev gates remain
explicitly unrun.

## SQLite sidecar final remediation — 2026-08-29

No post-commit or close-time code touches live WAL/SHM. Canonical database
mode protection is separate. A generic per-shard, PID-aware reentrant lease
now spans connect, WAL validation, all connection work, and close; it is not
operation-specific and releases on error or process exit. This was required
to prevent the source-only split-view result found after startup-only locking.
The exit-code-aware 80-pair source-MCP stress is green; candidate/live remain
unrun.

## D-CAND-006 final independent WAL/SHM stress adjudication — 2026-08-29

The locator-authority injections and successful source MCP assertions pass,
but source clearance remains **P1-blocked**. Repeating the four-round
two-process stress 20 times (80 independent races) produced a child that
closed stdout before its JSON-RPC response; the peer observed only a replayed
success. An independent 80-race harness also observed a source child exit
`-7` (SIGBUS). The descriptor lock and ephemeral-sidecar policy therefore do
not yet establish crash-free process-independent admission. No candidate or
live-dev result is promoted.
## D-CAND-006 final connection-lifetime lease review — 2026-08-29

Read-only evidence confirms the active remediation: a per-shard descriptor
lease is acquired before SQLite connect and remains held through the complete
connection/transaction/close lifetime; PID reset handles forked registries,
RLock nesting is reentrant, and kernel descriptor ownership releases an
abandoned process lease.  The inherited monotonic admission budget covers
locator, bootstrap, readiness, SQLite, WAL negotiation, reads, writes, and
receipts with capped waits.  The 80 simultaneous two-process source-MCP
subtests passed with exit-code-aware cleanup, and focused locator/lease safety
tests plus the full source set passed.

The remediation is not source-cleared: `_secure_sqlite_sidecar` remains a
latent WAL/SHM mutator through `os.fchmod`, contrary to the required
SQLite-exclusive lifecycle.  The active path is validation-only, but the
architectural contract applies to all source paths, not only currently
reachable ones.  Remove that mutating helper (and assert no WAL/SHM chmod,
create, unlink, or truncate in source/runtime tests) before granting P1
clearance.  Candidate/live-dev were not run.

## Latent-path remediation closure — 2026-08-29

`_secure_sqlite_sidecar` is removed. A source regression parses production
functions and fails any WAL/SHM path that contains a mutating filesystem call.
The 80-pair source-MCP stress remains green; candidate/live remain unrun.
## D-CAND-006 final post-removal clearance check — 2026-08-29

The sidecar mutator was removed.  Active store code validates extant WAL/SHM
paths only, while canonical database protection remains independent.  The
80-pair source-MCP stress and focused checks pass with clean child exits.

Source clearance is still **P1-blocked**: the AST conformance test is a
literal-text heuristic, not a non-bypassable assertion.  It does not model
computed sidecar names, aliases, or helper calls.  Strengthen the guard and
add a negative fixture that would fail for an indirect sidecar mutation;
until then, green runtime evidence is insufficient for the architectural
no-mutation guarantee.  Candidate/live-dev were not run.

Package-wide AST conformance now permits only named reviewed filesystem
capabilities and purposes, while live SQLite sidecars expose no mutation path.

## Mutation-boundary remediation closure — 2026-08-29

The earlier lexical-only P1 blocker is replaced by the enforceable runtime
mutation capability boundary. It scans every runtime module plus `cortex.py`,
rejecting direct operations, aliased imports, pathlib calls, dynamic lookup,
and unreviewed helper mutations. Only exact registered capability/purpose
pairs are allowed. No pair authorizes live WAL/SHM mutation.

The source MCP stress also observes the exec'd Python process and asserts that
no Cortex-originated Python filesystem operation mutates a WAL/SHM target.
SQLite's internal C lifecycle is not monkeypatched. Candidate/live-dev were
not run.

## Recursive capability-boundary closure — 2026-08-29

The static boundary is recursive and conservative. Any callable escape of an
OS/pathlib/shutil mutator through assignment, imports, nested modules,
defaults, or closures is a conformance failure, rather than a tolerated data
flow. The dynamic observer tracks descriptor-to-resolved-path ownership and
therefore detects low-level FD mutations as well as pathname operations.
## D-CAND-006 final filesystem-boundary review — 2026-08-29

The filesystem capability boundary is loaded by source tests and the
exec-child observer is active.  Current WAL/SHM handling is validation-only;
canonical DB protection and offline backup retention remain separate.  The
80-pair source-MCP stress and focused conformance/domain checks pass.

The source gate remains **P1-blocked**.  Static policy coverage is not
package-wide in the claimed semantic sense: assignment-bound aliases and
aliased `pathlib` constructors bypass it, the scanner is non-recursive, and
the runtime observer does not watch all low-level write/truncate operations.
Strengthen alias/data-flow
resolution and observer coverage before declaring the architecture cleared.
No candidate/live-dev run was performed.
## D-CAND-006 final filesystem-policy adversarial review — 2026-08-29

Recursive package scanning and FD observer coverage are active, and the
current source stress and focused suites pass.  The policy now rejects the
previous alias/pathlib/nested/default/closure cases.

The source gate remains **P1-blocked**: subscript calls whose callable is
itself a subscript (`os.__dict__["unlink"](...)`) and helper-returned or
callback-stored mutators still evade conformance.  Complete the call-target
and callable-flow analysis before source clearance.  No candidate/live-dev
run was performed.

The dynamic observer resolves every Python-opened FD path and follows dup/dup2
identity. It observes write, pwrite, writev, pwritev, ftruncate, fchmod, and
pathname mutation without attempting to wrap SQLite's C-owned descriptors.

Constructor/module identity now propagates through simple helper-return
summaries and subsequent calls. This closes returned `Path` constructor,
returned `pathlib`/`os`/`shutil` module, assigned-return, and chained-helper
indirection without expanding the Python/runtime proof scope.
## D-CAND-006 final callable-flow review — 2026-08-29

The policy and observer now cover the previously demonstrated subscript,
callable-storage, callback, closure, nested, and FD-dup/write-family cases.
The 80-pair source-MCP and focused conformance/domain suites remain green.

The source gate is still **P1-blocked** by one practical helper-flow escape:
an aliased `pathlib.Path` constructor returned by a helper can be invoked and
then mutated (`helper()(path).unlink()`).  Propagate constructor identity or
reject unknown callable returns before source clearance.  No candidate or
live-dev run was performed.
## D-CAND-006 final declared-scope clearance — 2026-08-29

The package-wide filesystem policy now rejects the complete declared
callable-flow set, including returned constructors/modules through one- and
two-hop helpers, while the child observer covers path and FD write-family
operations through duplication.  The focused conformance/domain suite and
80-pair source-MCP stress passed.

The source remediation is **cleared within the declared Python/runtime
scope**.  Candidate and live-dev qualification remain independent release
gates and were not run in this review.
