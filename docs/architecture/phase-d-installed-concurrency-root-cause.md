# Phase D installed-candidate concurrency root cause

Date: 2026-08-29  
Base version: `1.12.1`  
Status: **source remediation implemented; installed qualification remains required**

## Finding

The receipt-selected installed candidate fails the 80-pair gate at the first
concurrent `open_clarification` phase with `storage_busy`. This is not an
observation-journal registration failure and it is not an invalid installed
candidate path. The stress topology exposes a production scalability defect:
compact task-reference resolution performs a full scan of every project shard
under the same short per-request storage-admission deadline.

The current installed candidate was run through its real stdio MCP server with
the verified isolated candidate root and no checkout runtime import. The
focused stress command collected one failure in 17.51 seconds. Both workers in
the first pair received the sanitized `storage_busy` result during
`open_clarification`; no binding was issued. The complete test was therefore
correctly rejected.

## Lock and admission graph

```text
MCP initialize (each child)
  └─ claim_generation()
       └─ CODEX_HOME/.cortex-mcp-observations/.lock (blocking flock)
          └─ lease.json / generation request registration

MCP open_clarification (each child)
  └─ V12Store.for_task_ref(compact task ref)
       └─ enumerate every CODEX_HOME/cortex/v12/projects/p-* shard
            └─ _open_shard_for_task_ref()
                 └─ _connection()
                      └─ per-shard .sqlite-admission.lock
                           └─ SQLite read connection / WAL admission
  └─ canonical command read/write and receipt transaction
       └─ same inherited ~0.8s admission deadline

MCP terminal observation (each child)
  └─ EventJournal.emit()
       └─ generation/events.jsonl flock
            └─ bounded append-only observation only
```

The observation registration lock is a separate file and is acquired during
initialization. It blocks at the OS flock boundary and does not emit
`storage_busy`. The event journal also has a separate file lock and its
failures are explicitly downgraded to `observation=limited`; they cannot
change a canonical MCP result. Neither observation path overlaps the ledger
admission lock in the failing `open_clarification` request.

## Evidence and exact failing stage

Command used against the current receipt-selected installed root:

```text
PYTHONDONTWRITEBYTECODE=1 \
PYTHONPATH=/home/igovet/Web_Projects/codex-orchestration/plugins/cortex/scripts \
CORTEX_PHASE_D_CANDIDATE_ROOT=<receipt-selected-installed-plugin-root> \
CORTEX_PHASE_D_ISOLATED_OWNER_HOME=/home/igovet \
python3 -B -m pytest -q tests/test_phase_d_decision_qualification.py -k eighty -vv
```

Observed outcome: the two concurrent calls in the first pair both returned
the public `storage_busy` failure from `open_clarification`. The test's child
threads terminated normally, so this is bounded admission exhaustion rather
than a child hang or crash. The failure occurs before binding convergence,
recording, conflict checking, or worker publication.

The fixture intentionally creates 80 distinct project directories and two
children per project. Their `CandidateMcp` instances use the one isolated
candidate `CODEX_HOME`, so the canonical ledger has 80 distinct project
shards. This is not an unintended same-shard identity collision. However,
`for_task_ref()` has no task locator index: every one of the 160 processes
scans all 80 shards to resolve its compact task handle. Thus unrelated pairs
still contend on the admission locks of all shards while resolving a task
that belongs to only one shard. Once the first processes start these scans,
the inherited 0.8-second budget is exhausted before the target command can
admit its own shard.

## Why the existing record locator does not solve this

`record_locators` accelerates compact record references only. Task references
continue through `V12Store._for_task_ref_once`, which enumerates project
directories and opens each shard. Adding more SQLite busy timeout or retrying
the same call would only mask the topology problem and would not provide a
bounded guarantee under a growing number of shards. It would also make the
first-call contract dependent on load.

## Root correction recommendation

Make task-reference resolution server-owned and indexed, with the same
canonical-authority rule already used for records:

1. Add a durable task locator index in the V12 state root, keyed by the compact
   task suffix and retaining the canonical shard and task ID. Treat it as a
   derived accelerator, never as authority.
2. Resolve an indexed task suffix to one shard, verify the canonical task row
   in that shard, and carry the existing admission deadline only for that
   target shard.
3. If the task index is absent, stale, malformed, or busy, use a bounded
   exact recovery scan only as an explicit migration/recovery path. Repair the
   index best-effort after canonical verification; do not perform that scan on
   every normal first call.
4. Insert/update the task locator atomically with task creation and reconcile
   duplicate suffixes as an explicit ambiguity/conflict. Never guess across
   projects.
5. Ensure all resolver paths (`for_task_ref`, task anchors in command
   adapters, and any cross-project checks) consume this one resolver so no
   hidden fallback scan remains in the normal path.
6. Keep per-shard SQLite admission locks for the target shard. They protect
   filesystem/WAL admission but must not become a global semantic-operation
   lock.

The correction must be proved with a candidate-installed black-box stress
that uses one shared isolated `CODEX_HOME`, many independent project shards,
and 160 real stdio children. It must assert zero `storage_busy` results,
exactly one binding per pair, one non-replayed command result per pair, clean
child exits, and no hidden event-journal error. A fixture-only change that
gives every pair a different `CODEX_HOME` is not an acceptable fix: it would
remove the production contention pattern rather than correct it.

## Confidence and disposition

Confidence: **high (0.95)**. The failure is reproducible at the first
`open_clarification` call, the two processes per pair share the intended
target state, the 80 projects are distinct shards, and the source control
flow confirms that compact task resolution scans all shards while record
locators do not cover task references. Observation locks are disjoint and
non-authoritative by construction.

Disposition: **production architecture defect, not merely a fixture defect**.
The fixture is valid because it models multiple concurrent projects using the
same isolated runtime state root. The fixture should still gain a focused
resolver-topology assertion, but the runtime must be corrected before the
installed candidate can be qualified or live-dev can be trusted under this
load.

No source or runtime behavior was changed during this diagnostic review.

## Source remediation — v19 derived task locator

The production correction is now implemented as the forward-only
`v19-derived-task-locators` migration. It preserves the full 15-operation
catalogue and every existing orchestration owner; it changes only the private
server routing path for an already-emitted compact task reference.

Each canonical shard now stores `task_locator_publications`: the exact task ID,
its compact suffix, the shard/project hash, and a private full-ID fingerprint.
Task creation writes the canonical task row and this publication relation in
the same SQLite transaction. Only after that canonical commit does the
runtime best-effort publish the root-local `task-locators.db` accelerator. A
crash before sidecar publication is therefore a recoverable missing index,
never a locator-only task or a lost canonical task.

Normal `for_task_ref` resolution reads one sidecar row and opens only that
claimed shard. It independently verifies the task row, stored project root,
compact suffix, shard identity, and fingerprint before returning. The sidecar
is not authority: an absent, malformed, stale, wrong-project, or contended
sidecar uses one bounded canonical recovery scan, rejects zero or multiple
canonical matches, and repairs the proven suffix mapping best-effort. No
normal public task path enumerates shards. Existing record locators retain
the same non-authoritative rule.

The lock order is fixed: target-shard admission/transaction precedes a derived
locator write. Compact lookup holds no sidecar write lock while opening a
canonical shard. The correction does not extend the existing admission
deadline or retry budget.

Source evidence includes missing, malformed, fingerprint-tampered,
wrong-project, restart/recovery, migration/maintenance, and fail-closed tests,
plus 80 independent shards and 160 fresh concurrent first-call processes
sharing one `CODEX_HOME`. That source regression resolves every reference
without `storage_busy` and without using the legacy all-shard scan. It is not
installed-candidate or live qualification; those remain separate gates.
