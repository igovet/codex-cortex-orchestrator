# Phase D SQLite SIGBUS root-cause and sidecar-lifecycle decision

Status: **P1 source investigation complete; source qualification not yet
cleared.** This document is the canonical D-CAND-006 root-cause record. It
supersedes earlier contradictory status prose in Phase D notes; it does not
claim an exact-candidate or live-dev pass.

## Decision

SQLite, not Cortex, exclusively owns the lifetime and contents of a live
shard's `-wal` and `-shm` sidecars. Cortex may validate an extant sidecar and
apply owner-only permissions through an already-open descriptor during
serialized startup admission. Cortex must never create, replace, truncate,
unlink, or checkpoint a live sidecar as routine connection cleanup.

This is an architectural boundary, not a retry policy. The same invariant
applies to bootstrap, compact-reference resolution, ordinary reads/writes,
receipt execution, and process shutdown.

## Root cause

The historical implementation of `V12Store._materialize_sidecars` ran after
every `connection.close()`. When SQLite had removed an ephemeral sidecar, it
created a zero-length placeholder at the live `cortex.db-wal` or
`cortex.db-shm` pathname using exclusive file creation, then changed its mode.

That external recreation is the filesystem operation capable of invalidating
another process's SQLite shared-memory/WAL protocol. In the concurrent startup
window one process can be closing the last SQLite connection while another is
opening or mapping the WAL index. Reintroducing a distinct zero-length inode at
the same sidecar pathname breaks SQLite's ownership of the file generation and
can leave a process with an mmap-backed page whose expected backing file has
been replaced or resized. Access to such a mapping can terminate the process
with `SIGBUS`.

The underlying failure is therefore not an ordinary `BUSY`/`LOCKED` command
conflict, not command-receipt replay, and not record-locator repair. Retrying a
semantic mutation cannot make an invalid mmap safe.

The earlier pathname-only cleanup had a second, lower-severity race: it could
validate a sidecar and then call `chmod` after SQLite removed it. That produced
a misleading storage error, but a mode change by itself does not shrink,
replace, or invalidate a mapping.

## Current source assessment

The current uncommitted source changes remove the dangerous lifecycle behavior:

| Surface | Required behavior | Current assessment |
| --- | --- | --- |
| `V12Store._materialize_sidecars` | Validate optional WAL/SHM only; do not create or mutate them after close. | Implemented in the reviewed source. |
| `V12Store._secure_sqlite_sidecar` | Open an extant regular file without following a symlink; normalize its mode through that descriptor; re-observe disappearance; leave an absent sidecar absent. | Implemented in the reviewed source. |
| `V12Store._sqlite_admission_lock` | Hold one process-safe per-shard lease across connect, WAL-mode setup, sidecar validation, the complete connection/transaction lifecycle, and close. | Implemented in the reviewed source. |
| Record locator | Keep `record-locators.db` a separate reconstructible accelerator; its replacement must never touch `cortex.db-wal` or `cortex.db-shm`. | Separate from the SIGBUS root; still subject to its own authority tests. |
| Routine teardown | Close only the SQLite connection; do not run sidecar creation, replacement, deletion, or truncation. | Required invariant; source implementation must remain covered. |

An isolated source-MCP harness with explicit child exit-code capture completed
80 simultaneous two-process opens (160 ordinary `cortex.py` processes) without
an error, crash, duplicate binding, or leaked child. A separate earlier
80-race test helper observed a closed stdout but did not retain the server exit
code, so that occurrence cannot itself identify the failing syscall. The
independently observed `SIGBUS` remains a blocking historical failure until the
full exact-candidate matrix repeats this exit-code-aware test successfully.

This evidence is deliberately limited: it validates that the current source
survives one bounded reproduction shape, not that the candidate package or a
real live session is safe.

## File-operation ruling

| Operation on a live WAL/SHM sidecar | Mapping safety | Policy |
| --- | --- | --- |
| SQLite creates, deletes, resizes, checkpoints, or replaces it as part of its protocol | SQLite-owned protocol | Allowed only through SQLite. |
| Cortex creates a placeholder after close | Unsafe generation substitution | Prohibited. |
| Cortex unlinks, replaces, truncates, or writes it | Can invalidate an mmap-backed page or split a file generation | Prohibited unless an external maintenance operation has already proved global exclusivity. |
| Pathname `chmod` after a time-of-check | Does not normally invalidate an mmap, but is target-racy and can become a false storage failure | Do not use for live sidecars. |
| Descriptor `fchmod` of an extant regular sidecar | Changes inode metadata only; it does not resize or replace mapping backing on normal POSIX filesystems | Permitted only during the per-shard admission critical section, with disappearance treated as benign and unsafe type/permission faults fail-closed. |
| A post-commit permission sweep outside the admission lock | Not a direct SIGBUS cause, but needlessly touches a SQLite-owned live file while other processes can map it | Remove or avoid; startup admission is the only normalization point. |

`fchmod` is not a substitute for sidecar ownership. Its safety conclusion is
narrow: descriptor mode normalization is safe with respect to mmap backing;
it must not be expanded into creation, repair, deletion, or content mutation.

## Lock scope and cleanup

The per-shard lock is sufficient only for the Cortex-controlled transition:

```text
validate canonical database
  -> acquire descriptor-backed per-shard lock
  -> open SQLite connection
  -> ensure WAL mode and synchronous configuration
  -> validate already-existing sidecars without mutating them
  -> run the complete read/write transaction
  -> close SQLite connection
  -> release the per-shard lease
```

It is intentionally not a replacement for SQLite's semantic receipt locks,
but it is held through ordinary Cortex connection lifetimes after source stress
proved startup-only release allowed divergent sidecar views. The deliberate
tradeoff is per-shard Cortex connection serialization for deterministic first
call behavior. It also cannot prove that another arbitrary
SQLite client has no live mapping. Consequently, normal Cortex cleanup may not
delete or recreate live sidecars at all.

Maintenance that changes database generations (restore, replacement, direct
filesystem cleanup) needs a separate global-quiescence proof. The existing
maintenance restore contract already requires the service to be stopped;
routine checkpoint/VACUUM operations must use SQLite APIs and return a typed
busy/error result rather than manipulating sidecar paths. Backup-bundle
retention may unlink only sidecars inside a sealed, non-live backup bundle.

Test teardown must first prove every spawned MCP child has exited and every
pipe endpoint is closed before deleting its temporary `CODEX_HOME`. Cleanup is
not evidence of a pass and must not turn a missing response into a successful
race.

## Required remediation and tests

1. Keep the current no-materialization rule permanently: no live-sidecar
   `open(...O_CREAT...)`, `unlink`, `replace`, truncation, or content write
   outside SQLite.
2. Restrict any owner-only sidecar `fchmod` to the existing descriptor-based
   per-shard admission block. Remove the post-commit out-of-lock sidecar sweep
   or limit it to the canonical database only.
3. Add a source regression that instruments all live-sidecar filesystem calls
   and fails if routine bootstrap/open/close invokes creation, replacement,
   unlink, truncation, or a pathname-mode mutation.
4. Keep the deterministic disappearance-before-`fchmod` test. It must prove
   an absent sidecar is accepted and canonical reads continue, while symlink,
   wrong-type, and persistent permission failures remain fail-closed.
5. Replace the stress helper's opaque EOF report with captured server exit
   code, bounded sanitized stderr classification, binding/receipt counts, and
   a child-leak assertion. An EOF with a nonzero process exit is a failure.
6. Run at least 80 simultaneous two-process source-MCP races and then the
   same exit-code-aware test against the exact content-addressed candidate.
   Both require zero crashes, zero hangs, zero tool/storage errors, exactly one
   logical binding/receipt per race, and no unexplained successful mutation
   replay.
7. Do not start Phase D live-dev until the exact-candidate test is green. The
   later LLM-driven live session remains a separate acceptance gate.

## Qualification status

| Gate | Status | Evidence required before promotion |
| --- | --- | --- |
| Root-cause identification | Complete | Historical sidecar recreation is isolated as the unsafe file-generation operation. |
| Source implementation review | Conditional | Retain the no-materialization rule and eliminate the out-of-lock sidecar sweep. |
| Source stress | Not cleared | Repeat exit-code-aware 80-race test in the settled source tree. |
| Exact candidate | Blocked | Same test from the byte-identical staged package, with provenance verified. |
| Live-dev | Blocked | Only after candidate clearance; inspect visible coordinator and worker MCP evidence. |

## Final source hardening — 2026-08-29

The settled source removes every post-commit and close-time WAL/SHM operation.
Canonical `cortex.db` still receives strict descriptor protection through a
separate helper. During the per-shard lease Cortex only lstat-validates an
extant WAL/SHM sidecar; it does not create, chmod, replace, unlink, truncate,
or otherwise mutate it. The lease is PID-aware after fork, reentrant for a
nested command path, held through connection close, and released on ordinary
errors or process exit by the kernel flock mechanism.

The exit-code-aware source stress completed 80 simultaneous MCP pairs with
zero nonzero exits, EOFs, forced cleanup, tool errors, duplicate canonical
bindings, or duplicate receipts. Candidate and live-dev remain unrun.

## Latent-path conformance closure — 2026-08-29

The dormant `_secure_sqlite_sidecar` mutator is removed. AST-based source
conformance rejects any production function that constructs a `-wal` or
`-shm` path while containing open/chmod/fchmod/unlink/replace/truncate/write
operations. Canonical `cortex.db` protection is intentionally outside this
rule. The 80-pair source stress remains green; candidate/live remain unrun.

Package-wide mutation-capability conformance now rejects direct, aliased,
pathlib, dynamic, and unreviewed helper mutators; live SQLite sidecars remain
validation-only.

## Enforceable non-mutation boundary — 2026-08-29

The source guard is no longer limited to WAL/SHM literals. It rejects all
filesystem mutation paths in runtime source and `cortex.py` unless they are
registered as exact named safety capabilities. The registry grants no live
SQLite sidecar mutation authority. A child-process observer accompanies the
80-pair MCP stress and rejects Python-level WAL/SHM mutation without
intercepting SQLite's C implementation.

The proof also covers descriptor-based mutation: the stress observer maps
Python-opened FDs to their resolved paths and rejects low-level writes or
truncation of WAL/SHM. This does not change SQLite's exclusive C-owned
lifecycle.

The observer also preserves resolved path identity across `dup`/`dup2`, so a
Python FD alias cannot bypass the WAL/SHM mutation evidence boundary. Static
conformance rejects dynamic subscript call targets and all mutator callable
exports before the runtime can reach such a path.

Constructor and filesystem-module return summaries are also resolved before
an indirect call, preventing a helper-returned `Path` constructor from
creating a hidden Python-side WAL/SHM mutation route.

## Exact-candidate SIGBUS qualification — 2026-08-29

The no-materialization boundary was exercised from the fresh staged
content-addressed candidate `1.12.1+codex.sha256.eb691a9a49377dcc`, with
isolated `HOME`/`CODEX_HOME`, checkout imports/source mode removed, and
bytecode disabled. The exit-code/stderr-aware 80 simultaneous-pair candidate
stdio stress passed: no SIGBUS, nonzero exit, forced termination, hidden EOF,
stderr, duplicate binding/receipt, or changed-input mutation was observed.
Post-run inspection found no Python-created `-wal`/`-shm` sidecars in any
pair state root. This closes the exact-candidate D-CAND-006 evidence gate;
the next independent gate is the focused LLM-driven live-dev run.
