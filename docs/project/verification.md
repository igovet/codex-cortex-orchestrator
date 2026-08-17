# Verification

## Required commands

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
python3 scripts/cortex-cold-boot-smoke.py
python3 scripts/cortex-luna-high-eval.py
# Uses this checkout as the MCP source; it does not install, reinstall, or update Cortex.
python3 scripts/cortex-luna-high-eval.py --live --scenario automatic_sequential
python3 scripts/cortex-composite-benchmark.py --workers 8 --waves 5
python3 scripts/probe-fresh-cortex-plugin.py
python3 scripts/validate-cortex-marketplace.py
python3 -m py_compile plugins/cortex/scripts/cortex.py plugins/cortex/scripts/cortex_hook.py
bash -n scripts/sync-cortex.sh
./scripts/sync-cortex.sh --check
CORTEX_PYTHON=/absolute/path/to/python3.11 ./scripts/sync-cortex.sh --dry-run
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_cortex_invariants
python3 scripts/verify-cortex-release.py --require-tracked
```

`verify-cortex-release.py --require-tracked` runs only after a commit exists;
it validates `git archive HEAD`, not the mutable worktree.

## Current source-tree evidence

- `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v`
  passed **388 offline tests**.
- `python3 scripts/cortex-cold-boot-smoke.py` passed.
- `python3 scripts/cortex-luna-high-eval.py --live --scenario
  automatic_sequential` passed against this workspace's source tree. This is
  live source-mode validation in an isolated temporary project: it points its
  MCP server at the checkout and does **not** install, reinstall, update, or
  otherwise verify a user's installed Cortex plugin.

The source manifest currently declares `6.5.0`. These results are evidence for
the checked-out source only; release publication and installed-plugin
verification remain separate, explicitly requested actions.

## Current 6.5.0 source contract

- Cortex selects `python3` from `PATH` when `CORTEX_PYTHON` is unset. An
  explicit `CORTEX_PYTHON` value must be an absolute executable path; both
  paths are checked for Python 3.11+ and `tomllib`. Invalid explicit values
  fail before installer configuration changes, without falling back to
  `python3`. If the `PATH`-selected `python3` is Python 3.10 or lacks
  `tomllib`, the dry-run stops with a diagnostic naming the Python 3.11+
  requirement. The same resolver contract is enforced by the installed MCP
  and lifecycle-hook launcher, including paths containing spaces. For the
  persistent shell/GUI setup sequence, see [README.md's Select the Python
  runtime section](../../README.md#select-the-python-runtime); it covers the
  new-shell and Codex restart requirement and confirms that `/usr/bin/python3`
  is not modified.
- Resolver and launcher acceptance checks cover a selected interpreter,
  invalid-path no-write behavior, launcher execution, executable permissions,
  launcher-based MCP and five-hook configuration, marketplace validation, and
  fresh-plugin probing. A release candidate must pass the full regression,
  cold-boot, fresh-plugin, and tracked-archive checks before publication.
- The public executable was reduced from 11,831 to 7,576 lines. It is a small
  public composition and stdio entrypoint; focused runtime modules own the
  orchestration engine, SQLite ledger/migrations, artifact transport,
  delegation persistence, gate transitions, harvest validation, reports,
  questions, briefings, and MCP schema transport. Behavioral tests import the
  documented public surface rather than asserting function placement.
- `cortex.db` is the only coordination-state source for new tasks. Immutable
  reports, receipts, evidence, briefings, planning artifacts, handoffs, and
  manifests are catalogued in SQLite. Markdown/JSON task files are regenerated
  audit/Desktop projections; altering, deleting, or adding a projection cannot
  restore or modify coordination state. Existing pre-SQLite files are neither
  inspected, imported, resumed, altered, nor deleted.
- Database schema changes are numbered and checksummed through **schema v7**.
  First MCP access takes
  the project-ledger lock and applies only missing SQLite-to-SQLite migrations
  atomically; a failed or mismatched migration fails closed. Checksums cover
  migration version, name, and ordered normalized SQL content. Legacy
  name-only checksums are upgraded only after schema validation. The schema
  uses unbounded SQLite `TEXT`/`BLOB` values for content and indexed 32 KiB
  immutable artifact chunks for transport. Schema v7 separates deduplicated
  content blobs, task-scoped logical artifacts, and authorized filesystem
  exports; legacy v2 artifact rows are retained as migration evidence.
- Projection work is a durable SQLite outbox. A job is committed with the
  canonical artifact, claimed under a lease, materialized atomically with
  digest verification, then acknowledged in a separate transaction. Required
  briefings are capability files; reports and planning outputs are optional,
  rebuildable projections. Task directories and projection parents are lazy.
- Review and close reports carry a top-level structured `closure` sibling.
  Open P0/P1/P2 or blocking findings, and missing required verification, reopen
  the recorded target gate for rework; resolved or auditable non-self waivers
  are retained in SQLite rather than inferred from prose.
- Prune first commits a tombstone, removes the projection tree outside the
  state lock, and only then deletes canonical task rows in one final SQLite
  transaction. Failed filesystem removal leaves the task recoverable for a
  retry. Legacy v7/v3 files are never imported into the active ledger; their
  explicit maintenance route inventories, archives, and only then permits
  confirmation-bound deletion.
- `manage_orchestration(intent="maintenance")` provides read-only SQLite
  health inspection plus confirmation-bound checkpoint, SQLite-backup,
  backup-restore verification, optimize, vacuum, and projection reconciliation
  operations. It never treats WAL/SHM sidecars as application artifacts.
- Artifact APIs return metadata pages or bounded content parts. Signed opaque
  cursors bind the reader scope, task, artifact digest, and byte position.
  Transport never returns an unbounded report or briefing, and it preserves
  UTF-8 boundaries even for a one-byte requested page.
- Harvest regressions require a source-backed feature census, all canonical
  project documents and links, a structured coverage matrix, behavior-complete
  feature pages, independent completeness review, and zero unexplained
  unmapped surfaces. Harvest routes never stop for post-plan user approval.
- The installer preserves the explicit Cortex MCP approval mode and does not
  scan or remove earlier coordination state or unrelated plugin data. The
  fresh-plugin probe and `sync-cortex.sh --check` are installation-bound checks;
  they were not used as evidence for the source-mode live result above.

## What the regressions cover

Control-plane tests cover exact task identity, idempotent starts, concurrent
project isolation, transaction rollback checkpoints, scoped immutable
briefings, report/evidence/receipt integrity, compaction recovery, bounded
rework, stale-state pruning, worker-question resumption, and server-observed
close evidence. They also cover human-readable `Profile Module` labels and
attempt-unique native `task_name` values, so a host cannot resume a stale child
under a repeated display label.

Public API tests require exactly seven MCP tools: coordinator lifecycle
operations `start_orchestration`, `continue_orchestration`, and
`manage_orchestration`; worker `worker_question`, `record_report`, and
identity/digest-scoped `read_dispatch_briefing`; plus scoped predecessor
`read_worker_report`. Native worker prompts carry a compact bootstrap with the
exact immutable briefing path/digest; the worker cannot enumerate the ledger.

The automatic pipeline tests enforce the model-routing contract: Explorer uses
Luna, high-risk Security uses Sol at its complexity floor, and other profiles
select Luna or Terra according to their machine-readable profile policy. They
also validate the plan-approval boundary: ordinary user tasks may require it,
while harvest is automatic after its source-backed plan.

The release boundary rejects nested marketplaces, runtime state, bytecode,
symlinks, unsafe paths, private home paths, credential-like files, and missing
policy material from the archive. Structured public validation failures are not
exception-log events; actual MCP exceptions are redacted and correlate only
bounded identifiers.

<!-- GENERATED:START -->

## Authoritative command inventory

- `python3 -m unittest discover -s tests -v` — standard-library regression suite; CI source: [cortex.yml](../../.github/workflows/cortex.yml).
- `python3 scripts/cortex-cold-boot-smoke.py` — black-box JSON-RPC lifecycle smoke test; CI source: [cortex.yml](../../.github/workflows/cortex.yml).
- `python3 scripts/cortex-luna-high-eval.py` — deterministic Luna-high fixtures; add `--live --scenario automatic_sequential` for a real Luna-high parent run against the workspace source tree. It neither installs nor verifies an installed plugin. `SKIP` is not live evidence.
- `python3 scripts/cortex-composite-benchmark.py` — MCP call-count contract benchmark; it makes no latency claim.
- `python3 scripts/probe-fresh-cortex-plugin.py` — isolated fresh-plugin registration probe. `SKIP` means the Codex CLI is unavailable.
- `python3 scripts/validate-cortex-marketplace.py` — marketplace and plugin-contract validation.
- `python3 -m py_compile ...` — Python syntax compilation for runtime and helper modules.
- `bash -n scripts/sync-cortex.sh` — shell syntax check.
- `./scripts/sync-cortex.sh --check` — read-only installed-content and configuration-preservation check.
- `./scripts/sync-cortex.sh --dry-run` — no-write installation preview.
- `python3 scripts/verify-cortex-release.py --require-tracked` — blocking archive validation for committed `HEAD`.

<!-- GENERATED:END -->
