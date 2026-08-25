# Storage classification

Status: v11 implementation contract (task and governance schema v17).

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
capability issuer. Coordinator records carry `task_ref` and `coordinator_ref`;
worker records carry `task_ref` and `assignment_ref`. Capabilities survive only
through bounded, digest-checked handoffs. A missing or lost capability fails
closed. Native worker execution is the exact server-issued `spawn_agent` target
followed by exact `wait`, `read_worker_result`, and server-derived
continuation; session/environment values are not authorization.

## Classification matrix

| Data or path | Owner | Creator / reader | Status |
| --- | --- | --- | --- |
| ~/.codex/cortex/projects/p-<sha256>/cortex.db | SQLite schema v17 | Runtime writes; scoped APIs and compilers read | Canonical, private, retained by task policy |
| AttemptResult | SQLite attempt row | complete_attempt writes; coordinator and compilers read | Canonical semantic result plus server observations |
| AttemptEvent | SQLite append-only event stream | record_attempt_event writes; runtime reads | Canonical incremental evidence |
| repair_escrow | SQLite schema-v17 private immutable row | complete_attempt creates/reuses; same-attempt repair reads | Private rejected draft and diagnostics; task-cascaded, never public evidence |
| Read observations | SQLite task/attempt rows | briefing and predecessor-result reads create them | Canonical server observation |
| Immutable dispatch briefing | SQLite artifact catalog plus digest-checked briefing file | Runtime creates; worker reads exact grant | Required for native dispatch |
| results/*.json and results/markdown/*.md | Projection service | Runtime writes; humans and tooling read | Rebuildable view |
| journal, planning, handoff views, indexes | Projection service | Runtime writes; humans read | Rebuildable view |
| cortex.db-wal, cortex.db-shm, .state.lock | SQLite and coordination | Runtime | Incidental machinery, never evidence |

Filesystem views are private, regular, digest-checked, and disposable. A view
cannot authorize completion, a gate transition, or a read. A tombstone is
committed before pruning a view. Use SQLite-aware backup and maintenance.

On fresh task bootstrap, exact canonical v16 is the only recognized historical
predecessor. Cortex quarantines that complete namespace privately and creates a
clean schema-v17 ledger without migration or state import. The whole
namespace—SQLite database and sidecars, task/lane files, coordination files,
and lifecycle key—is quarantined together. No row, migration, task, lane,
sidecar, or capability is adopted into v17. V15 and older, unknown, or
tampered identities fail closed without archival. This recovery does not
become a user-visible form error. Unsafe filesystem boundaries remain
fail-closed.

Only the exact v16 predecessor is quarantined. Older or incompatible task
namespaces fail closed, and no prior row is imported into the current task
contract. The independent SQLite
schema migration and integrity history remains available for storage
maintenance and historical verification.

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

record_attempt_event is lossless and idempotent by event key. complete_attempt
validates semantic input and commits the canonical result. A finalization error
remains attached to that attempt.

## References

- [ledger_db.py](../../plugins/cortex/scripts/cortex_runtime/ledger_db.py)
- [cortex.py](../../plugins/cortex/scripts/cortex.py)
- [context_compiler.py](../../plugins/cortex/scripts/cortex_runtime/context_compiler.py)
- [handoff_compiler.py](../../plugins/cortex/scripts/cortex_runtime/handoff_compiler.py)
- [projection_service.py](../../plugins/cortex/scripts/cortex_runtime/projection_service.py)
- [verification.md](verification.md)
