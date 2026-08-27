# Operator maintenance

<!-- GENERATED:START -->

## Purpose and boundary

Cortex 12.1.0 includes a local administrator CLI for explicit maintenance of
one existing V12 project shard. It is implemented by
[v12_maintenance.py](../../../plugins/cortex/scripts/cortex_runtime/v12_maintenance.py)
and is deliberately outside the public MCP registry. It adds no twelfth tool,
accepts no `project_root` or arbitrary filesystem target, and begins every
operation from one exact V12 `task_id`. The task ID's embedded shard hash
selects the only database, backup, and projection paths the command may touch.

The CLI knows nothing about V11 and never writes below the target project. It
validates owner-only modes, regular-file/no-symlink boundaries, the V12
application ID, `PRAGMA user_version = 1`, the exact additive migration history,
required tables/indexes/triggers, project metadata, task/root binding, foreign
keys, integrity, WAL, and `synchronous=FULL` before a sensitive operation.
Output is one bounded sanitized JSON object on stdout; detailed database rows,
task/report content, paths, and raw exceptions are not emitted.

## Invocation

Run the module from the packaged `scripts` directory so `cortex_runtime` is on
Python's module path. In this checkout:

```bash
cd plugins/cortex/scripts
export CORTEX_TASK_ID='paste-exact-task-id-here'
PYTHONDONTWRITEBYTECODE=1 python3 -B -m cortex_runtime.v12_maintenance health \
  --task-id "$CORTEX_TASK_ID"
```

`health` is read-only. All mutating commands require an exact uppercase
confirmation token. A missing or mismatched token fails without applying the
requested action.

## Backup and restore

A backup is an online SQLite backup of the complete project shard, merely
anchored to the requested task. It is not a task-only export. The command first
requires a healthy live database, writes a new owner-only database plus bounded
manifest below
`~/.codex/cortex/v12/projects/p-<hash>/backups/<task-id>/<backup-id>/`, validates
the copy, records its SHA-256 digest and affected-task count, and never accepts
an arbitrary destination.

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B -m cortex_runtime.v12_maintenance backup \
  --task-id "$CORTEX_TASK_ID" \
  --confirm-action BACKUP
```

`restore` is strictly offline. Before invoking it, stop every normal Cortex MCP
process that can access the shard and verify that the database is quiescent.
`--confirm-service-stopped MCP_STOPPED` is the operator's assertion of that
fact; the CLI has no shared lock with the running MCP store and does not stop or
detect the service itself. Restore also requires the exact task ID, shard name,
sealed backup ID, and literal `RESTORE` token:

```bash
export CORTEX_BACKUP_ID='backup-paste-exact-id-here'
export CORTEX_SHARD='p-paste-64-lowercase-hex-shard-here'
PYTHONDONTWRITEBYTECODE=1 python3 -B -m cortex_runtime.v12_maintenance restore \
  --task-id "$CORTEX_TASK_ID" \
  --backup-id "$CORTEX_BACKUP_ID" \
  --confirm-action RESTORE \
  --confirm-task-id "$CORTEX_TASK_ID" \
  --confirm-shard "$CORTEX_SHARD" \
  --confirm-service-stopped MCP_STOPPED
```

Restore validates the selected backup and creates a fresh pre-restore recovery
backup before replacement. It reconfigures WAL/`synchronous=FULL` and reruns
health afterward. On a restore failure it attempts to recover from that fresh
backup and returns only a sanitized outcome. Never describe this command as an
online restore.

## SQLite maintenance

The remaining database commands require a healthy task/shard binding and their
exact action token:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B -m cortex_runtime.v12_maintenance checkpoint \
  --task-id "$CORTEX_TASK_ID" --mode PASSIVE --confirm-action CHECKPOINT

PYTHONDONTWRITEBYTECODE=1 python3 -B -m cortex_runtime.v12_maintenance optimize \
  --task-id "$CORTEX_TASK_ID" --confirm-action OPTIMIZE

PYTHONDONTWRITEBYTECODE=1 python3 -B -m cortex_runtime.v12_maintenance vacuum \
  --task-id "$CORTEX_TASK_ID" --confirm-action VACUUM
```

Checkpoint mode is one of `PASSIVE`, `FULL`, `RESTART`, or `TRUNCATE`.
`optimize` invokes SQLite's explicit optimizer. `vacuum` validates health both
before and after the operation. These are operator actions, not orchestration
steps, governance events, worker evidence, or prerequisites for a final answer.

## Projection regeneration and pruning

Regeneration re-renders only the exact task's host-private plan and finalized
report Markdown views. Task, decision, delegation, initiative, closure,
governance, handoff, index, and timeline records remain SQLite-only. Canonical
SQLite remains authoritative, and the normal renderer preserves direct-edit
conflicts:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B -m cortex_runtime.v12_maintenance \
  projection-regenerate --task-id "$CORTEX_TASK_ID" \
  --confirm-action REGENERATE_PROJECTIONS
```

Projection pruning is dry-run by default. It considers at most 10,000 exact
paths registered for the task and only non-ready rows marked `stale`,
`unavailable`, or `disabled`. It never removes `ready` views, conflicted or
digest-mismatched files, unmanaged files, a directory recursively, or canonical
ledger rows. An unsafe candidate makes the whole apply request fail before any
deletion.

```bash
# Preview only
PYTHONDONTWRITEBYTECODE=1 python3 -B -m cortex_runtime.v12_maintenance \
  projection-prune --task-id "$CORTEX_TASK_ID" --dry-run

# Apply the exact eligible set
PYTHONDONTWRITEBYTECODE=1 python3 -B -m cortex_runtime.v12_maintenance \
  projection-prune --task-id "$CORTEX_TASK_ID" --apply \
  --confirm-action PRUNE_PROJECTIONS
```

## Backup retention

Retention removes only explicitly named, complete, manifest-bound maintenance
backup bundles for the anchored task. It accepts 1–20 unique `--backup-id`
arguments, validates the whole set before mutation, and defaults to dry-run.
A valid bundle contains the required `cortex.db` and `manifest.json` plus, at
most, owner-private `cortex.db-wal` and `cortex.db-shm` support files created by
SQLite during local validation or restore. Any other member makes the bundle
unsafe. Apply mode revalidates this fixed non-recursive allowlist after the
confirmation, removes support files first, and never prunes the canonical
database, reports, plans, decisions, timeline, projection rows, or V11 state.

```bash
# Repeat --backup-id for each exact sealed bundle to evaluate
PYTHONDONTWRITEBYTECODE=1 python3 -B -m cortex_runtime.v12_maintenance retention \
  --task-id "$CORTEX_TASK_ID" --backup-id "$CORTEX_BACKUP_ID" --dry-run

PYTHONDONTWRITEBYTECODE=1 python3 -B -m cortex_runtime.v12_maintenance retention \
  --task-id "$CORTEX_TASK_ID" --backup-id "$CORTEX_BACKUP_ID" --apply \
  --confirm-action RETENTION
```

There is no automatic maintenance retention or cleanup. Operators remain
responsible for their backup schedule, quiescence, free space, encrypted host
storage, and carefully scoped deletion choices.

## Verification

Release evidence must prove that the CLI is packaged without changing the
eleven-tool MCP registry; rejects malformed/cross-shard task anchors, unsafe
paths, modes, schemas, backup manifests, and confirmation strings; creates and
validates project-shard backups; requires explicit offline restore
acknowledgement; preserves canonical data during projection/backup retention;
never writes below `project_root` or touches V11; and returns sanitized JSON
with meaningful exit status.

See [storage classification](../../project/storage-classification.md),
[project verification](../../project/verification.md),
[human-readable task views](../human-readable-task-views/index.md), and the
[security policy](../../../SECURITY.md).

<!-- GENERATED:END -->
