# Storage classification

Status: v11 implementation contract (task and governance schema v18).

## Decision

cortex.db is the only authoritative mutable store for a new v11 task. The
default database is host-private at ~/.codex/cortex/projects/p-<sha256>/cortex.db.
A validated private CORTEX_HOST_STATE_DIR may override that root only when it is
outside the workspace. SQLite is the transaction boundary.

The canonical worker protocol is AttemptResult plus append-only AttemptEvent.
The worker supplies semantic facts; the server owns attempt identity, task
revision, dispatch/profile/phase, timestamps, changed-file calculation,
workspace observations, checks, verification observations, and read
observations. ContextCompiler and HandoffCompiler build scoped target-specific views from this
state.

`start_orchestration` is the sole task creator and initial coordinator
capability issuer. Coordinator records carry private task authority; worker
records carry only exact native dispatch authority. Capabilities survive only
through bounded, digest-checked handoffs. A missing or lost capability fails
closed. Native worker execution is the exact server-issued `spawn_agent` target
followed by exact `wait`, an action-specific canonical wave read, and server-derived
continuation; session/environment values are not authorization.

## Classification matrix

| Data or path | Owner | Creator / reader | Status |
| --- | --- | --- | --- |
| ~/.codex/cortex/projects/p-<sha256>/cortex.db | SQLite schema v18 | Runtime writes; scoped APIs and compilers read | Canonical, private, retained by task policy |
| AttemptResult | SQLite attempt row | submit/repair operations write; coordinator and compilers read | Canonical semantic result plus server observations |
| AttemptEvent | SQLite append-only event stream | record_attempt_event writes; runtime reads | Canonical incremental evidence |
| repair_escrow | SQLite schema-v18 private immutable row | submit/repair operations create or reuse | Private rejected draft and diagnostics; task-cascaded, never public evidence |
| Read observations | SQLite task/attempt rows | briefing and predecessor-result reads create them | Canonical server observation |
| Immutable dispatch briefing | SQLite artifact catalog plus digest-checked briefing file | Runtime creates; worker reads exact grant | Required for native dispatch |
| results/*.json and results/markdown/*.md | Projection service | Runtime writes; humans and tooling read | Rebuildable view |
| journal, planning, handoff views, indexes | Projection service | Runtime writes; humans read | Rebuildable view |
| cortex.db-wal, cortex.db-shm, .state.lock | SQLite and coordination | Runtime | Incidental machinery, never evidence |

Filesystem views are private, regular, digest-checked, and disposable. A view
cannot authorize completion, a gate transition, or a read. A tombstone is
committed before pruning a view. Use SQLite-aware backup and maintenance.

Only the exact signed V11 v1--v8 database lineage is recognized as migration
input. Cortex validates its complete ordered lineage and upgrades it atomically
to schema v18. Any prior task authority preserved by that operation remains
private and non-selectable migration state; no public task reference can choose
or recover it. Unknown, missing, unsigned, reordered, or tampered histories,
and unsafe filesystem boundaries, fail closed rather than being quarantined,
guessed, imported, or treated as a fresh ledger. The independent SQLite schema
migration and integrity history remains available for storage maintenance and
historical verification.

## Lifecycle

~~~text
worker events
     ↓
AttemptResult: WORK_COMPLETED
     ↓ server observations
FINALIZING ── retry same attempt ──> COMPLETED
     ├── BLOCKED
     └── FAILED
~~~

`record_attempt_event` is lossless and idempotent by event key. `submit_attempt`
validates semantic input and commits the canonical result; `repair_attempt`
applies only a server-issued same-attempt correction. A finalization error
remains attached to that attempt.

## References

- [ledger_db.py](../../plugins/cortex/scripts/cortex_runtime/ledger_db.py)
- [cortex.py](../../plugins/cortex/scripts/cortex.py)
- [context_compiler.py](../../plugins/cortex/scripts/cortex_runtime/context_compiler.py)
- [handoff_compiler.py](../../plugins/cortex/scripts/cortex_runtime/handoff_compiler.py)
- [projection_service.py](../../plugins/cortex/scripts/cortex_runtime/projection_service.py)
- [verification.md](verification.md)
