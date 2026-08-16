# Verification

## Required commands

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
python3 scripts/cortex-cold-boot-smoke.py
python3 scripts/cortex-luna-high-eval.py
python3 scripts/cortex-luna-high-eval.py --live --scenario automatic_sequential
python3 scripts/cortex-composite-benchmark.py --workers 8 --waves 5
python3 scripts/probe-fresh-cortex-plugin.py
python3 scripts/validate-cortex-marketplace.py
python3 -m py_compile plugins/cortex/scripts/cortex.py plugins/cortex/scripts/cortex_hook.py
bash -n scripts/sync-cortex.sh
./scripts/sync-cortex.sh --check
python3 scripts/verify-cortex-release.py --require-tracked
```

`verify-cortex-release.py --require-tracked` runs only after a commit exists;
it validates `git archive HEAD`, not the mutable worktree.

## Current 6.4.1 evidence

- The full standard-library suite passed 320 tests: 250 control-plane,
  66 invariant, and 4 SQLite-ledger tests.
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
- Database schema changes are numbered and checksummed. First MCP access takes
  the project-ledger lock and applies only missing SQLite-to-SQLite migrations
  atomically; a failed or mismatched migration fails closed. The schema uses
  unbounded SQLite `TEXT`/`BLOB` values for content and indexed 32 KiB immutable
  artifact chunks for transport.
- Artifact APIs return metadata pages or bounded content parts. Signed opaque
  cursors bind the reader scope, task, artifact digest, and byte position.
  Transport never returns an unbounded report or briefing, and it preserves
  UTF-8 boundaries even for a one-byte requested page.
- Harvest regressions require a source-backed feature census, all canonical
  project documents and links, a structured coverage matrix, behavior-complete
  feature pages, independent completeness review, and zero unexplained
  unmapped surfaces. Harvest routes never stop for post-plan user approval.
- The installer preserves the explicit Cortex MCP approval mode, does not scan
  or remove earlier coordination state or unrelated plugin data, and the
  fresh-plugin probe verifies the installable package in isolated HOME/CODEX_HOME
  directories. Build `6.4.1+codex.20260816170348` is installed and
  content-verified. Its final live Luna-high automatic-sequential run passed:
  one task/start, seven verified worker phases, scoped report reads and
  continue operations, no failed public calls, server-observed close evidence,
  completed handoff, and manifest-snapshot cleanup.

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
- `python3 scripts/cortex-luna-high-eval.py` — deterministic Luna-high fixtures; add `--live --scenario automatic_sequential` for a real Luna-high parent run. `SKIP` is not live evidence.
- `python3 scripts/cortex-composite-benchmark.py` — MCP call-count contract benchmark; it makes no latency claim.
- `python3 scripts/probe-fresh-cortex-plugin.py` — isolated fresh-plugin registration probe. `SKIP` means the Codex CLI is unavailable.
- `python3 scripts/validate-cortex-marketplace.py` — marketplace and plugin-contract validation.
- `python3 -m py_compile ...` — Python syntax compilation for runtime and helper modules.
- `bash -n scripts/sync-cortex.sh` — shell syntax check.
- `./scripts/sync-cortex.sh --check` — read-only installed-content and configuration-preservation check.
- `./scripts/sync-cortex.sh --dry-run` — no-write installation preview.
- `python3 scripts/verify-cortex-release.py --require-tracked` — blocking archive validation for committed `HEAD`.

<!-- GENERATED:END -->
