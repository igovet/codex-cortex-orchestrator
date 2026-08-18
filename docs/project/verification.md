# Verification

## Required commands

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
python3 scripts/cortex-cold-boot-smoke.py
python3 scripts/cortex-luna-high-eval.py
# Uses this checkout as the MCP source; it does not install, reinstall, or update Cortex.
# Fast source-mode transport/follow-up probe used during development.
python3 scripts/cortex-luna-high-eval.py --live --scenario follow_up_partial
# Full lifecycle live scenario for a release gate.
python3 scripts/cortex-luna-high-eval.py --live --scenario automatic_sequential
# Optional per-scenario timeout override (10..7200 seconds; default 1800).
python3 scripts/cortex-luna-high-eval.py --live --scenario automatic_sequential --live-timeout-seconds 900
# Optional, explicit retention of sanitized failure metadata under /tmp.
python3 scripts/cortex-luna-high-eval.py --live --scenario automatic_sequential --retain-failure-metadata
python3 scripts/cortex-composite-benchmark.py --workers 8 --waves 5
python3 scripts/probe-fresh-cortex-plugin.py
PYTHONDONTWRITEBYTECODE=1 python3 scripts/cortex-host-preflight.py
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

- The full Python 3.12.3 discovery run passed all 485 tests. Focused question,
  lazy-filesystem, approval-freshness, pipeline-order, briefing-completeness,
  log-cap, compatibility, and evaluator-contract regressions also passed.
- Cold boot passed with 9 reports, 9 worker attempts, 8 continuation calls,
  plan approval, and a parallel wave.
- Marketplace and plugin validation, Python compilation, Bash syntax, and
  `git diff --check` passed. Deterministic evaluator fixtures for
  `automatic_sequential`, `compact_parallel`, and `blocked_resume` completed.
- The isolated fresh-plugin probe passed for
  `8.0.0+codex.20260818180000` in temporary `HOME`/`CODEX_HOME`.
- The targeted source-mode `follow_up_partial` live scenario passed in 22
  seconds: it created exactly one linked v2 corrective task, preserved the
  completed source task, prepared the first dispatch, and made no failed public
  calls. The longer `automatic_sequential` scenario was not run to completion
  after the user requested faster targeted validation; it is still a separate
  release-only gate. Both modes use this workspace's source tree in an isolated
  temporary project and do **not** install, reinstall, update, or otherwise
  verify a user's installed Cortex plugin.
- The reportless-stop recovery coverage includes terminal failed-stop handling,
  exact failed receipts, mixed-wave slot preservation, bounded retry failure,
  and the ordering-sensitive PostToolUse case where an earlier reportless
  attempt remains visible after a later attempt completes.

The live evaluator emits newline-delimited JSON progress records while the
parent runs. Each record has `type: "cortex_live_progress"`, the scenario, and
an event such as `parent_started`, sanitized `cortex_mcp_call`, aggregate
`ledger_progress`, `parent_activity`, `host_stderr`, or `parent_finished`.
While the process is alive it emits a bounded heartbeat every 15 seconds;
heartbeats report elapsed time, last activity class/age, and whether the
process is still running. Only the allowlisted public Cortex tool name,
bounded status, boolean `ok`, safe ledger counts/statuses/gates, and safe event
names are exposed. Prompts, arguments, report content, tool results, source
paths, and arbitrary host stderr content are never streamed or retained.
Host stream events are capped at 512 emitted and 512 retained records; excess
events produce a bounded suppression marker and increment the reported dropped
count. Host stream classification may also emit `parent_completed`,
`parent_turn`, `host_output`, and `host_event`; bounded-output handling emits
`host_events_suppressed`. Timeout or signal paths emit
`termination_requested`, optional `termination_escalated`, `terminated`, and a
final `parent_finished` record.

Each live scenario has a 1,800-second default timeout, overridable only from
10 through 7,200 seconds with `--live-timeout-seconds`. The Codex parent starts
in its own process group. Timeout, interrupt, or termination cleanup sends
`SIGTERM`, waits ten seconds, then escalates to `SIGKILL`, reaping the group and
emitting termination records. On normal or supervised exits, the evaluator
removes its private runtime home and temporary source project. A crash or
external `SIGKILL` can prevent `finally` cleanup and leave OS-temporary
residue; cleanup refuses unexpected or symlinked paths. Failure metadata is
not retained by default; the result then reports `failure_metadata: "not_retained"`.
With the explicit
`--retain-failure-metadata` opt-in, a `failure_artifacts` directory under
`/tmp` contains only sanitized `progress.json` (the result plus the last
bounded progress events); raw Codex output and the temporary project are not
copied there.

Source-mode uses `codex exec --ephemeral --ignore-user-config` with the MCP
command and server path from this checkout. Every run receives a private 0700
`HOME`/`CODEX_HOME` plus private temporary, cache, config, and data
directories. Only the least-privilege runtime environment (`PATH`, locale,
terminal, and the selected authentication source) is passed to Codex. If
`OPENAI_API_KEY` or `CODEX_API_KEY` is present, that key is selected without
logging its value; otherwise the configured Codex home `auth.json` is copied
only when it is a private regular non-symlink file no larger than 1 MiB, using
no-follow/inode checks, into a 0600 private copy. Authentication contents are
never logged or streamed. It does not call `sync-cortex.sh`, install a plugin,
modify global Codex configuration, or prove the behavior of an installed
plugin.
Use the fresh-plugin probe, `sync-cortex.sh --check`, and tracked-release
verification separately for installation/package evidence. A live `SKIP`
means the Codex runtime is unavailable and is not live evidence.

The source manifest declares `8.0.0+codex.20260818180000`. These results are
evidence for the checked-out source only; release publication and installed-plugin
verification remain separate, explicitly requested actions.

## Current 8.0.0 source contract

- Cortex selects `python3` from `PATH` when `CORTEX_PYTHON` is unset. An
  explicit `CORTEX_PYTHON` value must be an absolute executable path; both
  paths are checked for Python 3.11+ and `tomllib`. Invalid explicit values
  fail before installer configuration changes, without falling back to
  `python3`. If the `PATH`-selected `python3` is Python 3.10 or lacks
  `tomllib`, the dry-run stops with a diagnostic naming the Python 3.11+
  requirement. The same resolver contract is enforced by the installed MCP
  and lifecycle-hook launcher, including paths containing spaces. For the
  persistent shell/GUI setup sequence, see [README.md's system requirements
  section](../../README.md#1-system-requirements); it covers the
  new-shell and Codex restart requirement and confirms that `/usr/bin/python3`
  is not modified.
- Resolver and launcher acceptance checks cover a selected interpreter,
  invalid-path no-write behavior, launcher execution, executable permissions,
  launcher-based MCP and five-hook configuration, marketplace validation, and
  fresh-plugin probing. A release candidate must pass the full regression,
  cold-boot, fresh-plugin, and tracked-archive checks before publication.
- The public executable is a composition and stdio entrypoint; focused runtime modules own the
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
- Database schema changes are numbered and checksummed through **schema v8**.
  First MCP access takes
  the project-ledger lock and applies only missing SQLite-to-SQLite migrations
  atomically; a failed or mismatched migration fails closed. Checksums cover
  migration version, name, and ordered normalized SQL content. Legacy
  name-only checksums are upgraded only after schema validation. The schema
  uses unbounded SQLite `TEXT`/`BLOB` values for content and indexed 32 KiB
  immutable artifact chunks for transport. Schema v8 adds task/plan revisions,
  native worker sessions, attempt messages, trace/tool observations, and
  question-batch storage; schema v7 separates deduplicated
  content blobs, task-scoped logical artifacts, and authorized filesystem
  exports; legacy v2 artifact rows are retained as migration evidence.
- Projection work is a durable SQLite outbox. A job is committed with the
  canonical artifact, claimed under a lease, materialized atomically with
  digest verification, then acknowledged in a separate transaction. Required
  briefings are capability files; reports and planning outputs are optional,
  rebuildable projections. Task directories and projection parents are lazy.
- Every gate report carries a top-level structured `gate_result` sibling. The older
  `closure` sibling remains a review/close compatibility alias. Open P0/P1/P2
  or blocking findings, and missing required verification, reopen the recorded
  target gate for rework; resolved or auditable non-self waivers are retained
  in SQLite rather than inferred from prose.
- Active user corrections use a task revision and resume addressable native
  worker sessions in place; completed-task corrections remain linked
  `follow_up` tasks. Schema v8 stores the revision, session, and atomic
  question-batch identity; `ask_batch`/`poll_batch` cover 1–32 questions with
  canonical answers. The main-chat UI keeps one durable batch ref while
  presenting one native question per step, checkpoints accepted answers, and
  resumes after cancellation at the next unanswered question.
- Required post-plan review is surfaced through native **Approve/Cancel** UI.
  Approve emits a localized plan-approved notice and dispatches the next wave;
  Cancel is silent and leaves the plan pending for the next user message.
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
- The installer enforces Cortex MCP `default_tools_approval_mode = "approve"`
  on install, including a clean config, while preserving it across remove/add,
  and does not
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
- `python3 scripts/cortex-luna-high-eval.py` — deterministic Luna-high fixtures; add `--live --scenario automatic_sequential` for a streamed, sanitized source-mode parent run against the workspace source tree. It neither installs nor verifies an installed plugin. `--live-timeout-seconds` accepts 10..7200 seconds and defaults to 1800; `--retain-failure-metadata` explicitly opts into bounded sanitized `/tmp` metadata. `SKIP` is not live evidence.
- `python3 scripts/cortex-composite-benchmark.py` — MCP call-count contract benchmark; it makes no latency claim.
- `python3 scripts/probe-fresh-cortex-plugin.py` — isolated fresh-plugin registration probe. `SKIP` means the Codex CLI is unavailable.
- `PYTHONDONTWRITEBYTECODE=1 python3 scripts/cortex-host-preflight.py` — read-only host
  diagnostic for Codex CLI, Python 3.11+/`tomllib`, plugin launcher, same-user
  cache, exact `cortex@cortex` registration, MCP approval configuration, and
  lifecycle-hook trust. Its JSON output includes `mcp.status` (`READY` or
  `BLOCKED`) and `mcp.blocking_checks`; `READY` requires every emitted check to
  pass. A nonzero result identifies the failed prerequisite; it never installs
  software or changes Codex configuration. See the [SSH host troubleshooting
  runbook](ssh-hetzner-troubleshooting.md) for same-user remediation and the
  bounded reportless-stop recovery sequence.
- `python3 scripts/validate-cortex-marketplace.py` — marketplace and plugin-contract validation.
- `python3 -m py_compile ...` — Python syntax compilation for runtime and helper modules.
- `bash -n scripts/sync-cortex.sh` — shell syntax check.
- `./scripts/sync-cortex.sh --check` — read-only installed-content and configuration-preservation check.
- `./scripts/sync-cortex.sh --dry-run` — no-write installation preview.
- `python3 scripts/verify-cortex-release.py --require-tracked` — blocking archive validation for committed `HEAD`.

<!-- GENERATED:END -->
