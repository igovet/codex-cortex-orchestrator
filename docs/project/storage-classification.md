# Storage classification

Status: v10 implementation contract (task and governance schema v15).

## Decision

cortex.db is the only authoritative mutable store for a new cortex/v10 task. The
default database is host-private at ~/.codex/cortex/projects/p-<sha256>/cortex.db.
A validated private CORTEX_HOST_STATE_DIR may override that root only when it is
outside the workspace. SQLite is the transaction boundary.

The canonical worker protocol is AttemptResult plus append-only AttemptEvent.
The worker supplies semantic facts; the server owns attempt identity, task
revision, dispatch/profile/phase, timestamps, changed-file calculation,
workspace observations, checks, verification observations, and read
observations. ContextCompiler and HandoffCompiler build scoped target-specific views from this
state.

## Classification matrix

| Data or path | Owner | Creator / reader | Status |
| --- | --- | --- | --- |
| ~/.codex/cortex/projects/p-<sha256>/cortex.db | SQLite schema v15 | Runtime writes; scoped APIs and compilers read | Canonical, private, retained by task policy |
| AttemptResult | SQLite attempt row | complete_attempt writes; coordinator and compilers read | Canonical semantic result plus server observations |
| AttemptEvent | SQLite append-only event stream | record_attempt_event writes; runtime reads | Canonical incremental evidence |
| Read observations | SQLite task/attempt rows | briefing and predecessor-result reads create them | Canonical server observation |
| Immutable dispatch briefing | SQLite artifact catalog plus digest-checked capability file | Runtime creates; worker reads exact grant | Required for native dispatch |
| results/*.json and results/markdown/*.md | Projection service | Runtime writes; humans and tooling read | Rebuildable view |
| journal, planning, handoff views, indexes | Projection service | Runtime writes; humans read | Rebuildable view |
| cortex.db-wal, cortex.db-shm, .state.lock | SQLite and coordination | Runtime | Incidental machinery, never evidence |

Filesystem views are private, regular, digest-checked, and disposable. A view
cannot authorize completion, a gate transition, or a read. A tombstone is
committed before pruning a view. Use SQLite-aware backup and maintenance.

On fresh task bootstrap, a host namespace with non-canonical migration history
or schema is treated as an untrusted prior namespace: Cortex archives the
complete namespace privately and creates a clean schema-v15 ledger without
migration or state import. This recovery does not become a user-visible form
error. Unsafe filesystem boundaries remain fail-closed.

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
