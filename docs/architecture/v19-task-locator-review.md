# v19 task-locator independent review

Date: 2026-08-29  
Base version: `1.12.1`  
Review mode: source-only, read-only review with isolated owner-only test roots  
Disposition: **source-cleared; installed-candidate and live gates remain separate**

## Scope

This review independently checked the v19 correction for the installed
80-shard/160-process first-call failure. It covered migration and maintenance
shape, canonical task publication, derived-sidecar behavior, the indexed hot
path, bounded recovery, cross-project and tamper handling, restart repair,
resolver reuse by public task-anchored paths, lock ordering, and the
filesystem-mutation policy registry.

## Source evidence

The focused gate ran with fresh mode-0700 `HOME` and `CODEX_HOME` directories,
without stable-profile state:

```text
HOME=<fresh-0700-root> CODEX_HOME=<fresh-0700-root>/.codex \
PYTHONPATH=/home/igovet/.local/lib/python3.12/site-packages:/home/igovet/Web_Projects/codex-orchestration/plugins/cortex/scripts \
PYTHONDONTWRITEBYTECODE=1 python3 -B -m pytest -q \
  tests/test_task_locators.py tests/test_command_receipts.py \
  tests/test_clarification_holds.py tests/test_mcp_event_journal.py \
  tests/test_exact_session_observation_lease.py tests/test_candidate_provenance.py \
  tests/test_phase_d_candidate_root_cause.py tests/test_v17_maintenance_schema.py \
  tests/test_domain_public_api_contract.py tests/test_public_mcp_first_call_conformance.py
```

Result: **84 passed, 30 subtests passed**. The task-locator suite includes the
real source-storage topology test with 80 independent shards and 160 fresh
processes sharing one state root; all first compact-task resolutions completed
without `storage_busy`, and no legacy all-shard scan was permitted on the
indexed path. The command-receipt policy suite separately passed **20 tests
and 106 subtests**.

## Invariant review

| Invariant | Result | Evidence |
|---|---|---|
| Forward-only v19 migration/backfill | Pass | `v19-derived-task-locators` is appended to the migration chain; existing canonical tasks receive `task_locator_publications`; maintenance required tables/columns/indexes include the relation. |
| Canonical task publication | Pass | Task row and `task_locator_publications` are inserted in the same shard `BEGIN IMMEDIATE` transaction. A crash before sidecar publication cannot create a locator-only task. |
| Sidecar non-authority | Pass | `task-locators.db` is only an accelerator; missing, malformed, stale, wrong-project, or busy entries fall to canonical verification/recovery. No sidecar row is accepted without fingerprint, suffix, shard, and canonical-row checks. |
| Normal hot path | Pass | `for_task_ref` probes one derived row, opens only its claimed shard, and rejects a legacy scan in the normal indexed regression. |
| Recovery and ambiguity | Pass | Exact bounded recovery rejects zero/multiple canonical matches; a proven single match repairs the derived entry best-effort. It does not guess or replace a binding. |
| Cross-project and tamper safety | Pass | Wrong shard/task/fingerprint is not authoritative; canonical verification and exact recovery preserve the original target or fail closed. |
| Restart behavior | Pass | Rebuilt process-local locks and sidecar repair preserve the same canonical task route after restart/recovery. |
| Public resolver coverage | Pass | Task-anchored service paths call the shared `_task_store` resolver; linked task references use `V12Store.for_task_ref`; no second direct all-shard resolver was found. |
| Lock order | Pass | Canonical target-shard admission/transaction precedes derived sidecar publication. Lookup does not hold a sidecar write lock while opening the canonical shard. The admission budget/retry constants were not changed. |
| Filesystem policy | Pass | v19 task-locator write/rebuild callables are explicitly registered in `filesystem_policy.py`; runtime payload includes that module and policy tests pass. |

## Remaining release boundary

This review clears the source architecture and regression gate only. It does
not claim that the receipt-selected installed candidate has been refreshed or
qualified after v19, and it does not claim a tmux/live-dev result. The next
required check is the same full Phase D suite through the exact installed
candidate, with its real stdio MCP children and shared isolated
`CODEX_HOME`. That gate must confirm the indexed topology under the packaged
runtime, then the LLM-driven attached live-dev scenario must be rerun.

No source, runtime, candidate cache, stable profile, database, or live session
was modified during this review.
