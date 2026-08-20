# Verification

## Required commands

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
python3 scripts/cortex-cold-boot-smoke.py
python3 scripts/cortex-luna-high-eval.py
# Uses this checkout as the MCP source; it does not install, reinstall, or update Cortex.
# Fast source-mode transport/follow-up probe used during development.
python3 scripts/cortex-luna-high-eval.py --live --scenario follow_up_partial
# Narrow real-host finding handoff: a public-API source prelude opens the review
# finding; real corrective documentation -> fresh review -> resolved.
# This scenario has a hard 300-second deadline; --live-timeout-seconds may only reduce it.
python3 scripts/cortex-luna-high-eval.py --live --scenario finding_rework_documentation
# Full C2 real-host lifecycle: real opening Review → Documentation → fresh
# Review → Close → completed final handoff (hard 1800 s).
python3 scripts/cortex-luna-high-eval.py --live --scenario finding_rework_documentation_full
# Full lifecycle live scenario for a release gate.
python3 scripts/cortex-luna-high-eval.py --live --scenario automatic_sequential
# Automatic C3 governance lifecycle: no governance mode is supplied by the caller.
python3 scripts/cortex-luna-high-eval.py --live --scenario automatic_governance
# Optional per-scenario timeout override (10..7200 seconds; default 1800).
python3 scripts/cortex-luna-high-eval.py --live --scenario automatic_sequential --live-timeout-seconds 900
# Optional, explicit retention of sanitized failure metadata under /tmp.
python3 scripts/cortex-luna-high-eval.py --live --scenario automatic_sequential --retain-failure-metadata
python3 scripts/cortex-composite-benchmark.py --workers 8 --waves 5
python3 -B scripts/cortex-manifest-benchmark.py --files 50000 --max-seconds 30
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

## Focused finding-handoff regression

Run this short deterministic public-transport subset while working on report,
finding, rework, or briefing contracts. It does not require a Codex model,
plugin install, or live host session:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONHASHSEED=0 python3 -B -m unittest -v \
  tests.test_finding_transitions \
  tests.test_live_finding_rework_contract \
  tests.test_cortex_control.ControlPlaneTests.test_closure_finding_is_canonical_across_review_close_and_resolved_rework \
  tests.test_cortex_control.ControlPlaneTests.test_corrective_documentation_cannot_resolve_its_review_finding \
  tests.test_cortex_control.ControlPlaneTests.test_same_gate_resolution_requires_finding_bound_correction_receipt \
  tests.test_cortex_control.ControlPlaneTests.test_pass_gate_result_rejects_open_findings_at_report_intake
```

The first control-plane test proves `review → documentation → fresh review →
close`, including exact immutable source/correction handoffs, stale semantic
route rejection, and the server-bound resolved transition. The second is the
authority negative case: Documentation may report a correction but cannot
resolve Review's finding, including by repeating its fingerprint as open. The
third test verifies that every route—including same-gate correction—needs a
separate correction receipt bound to the exact fingerprint and origin report.
The final test rejects a self-contradictory `pass` plus open finding before an
immutable report or receipt is written.

For a real-host counterpart, run the dedicated source-mode scenario below. It
uses a minimal C1 workspace and exactly one controlled fingerprint,
`live-documentation-finding-001`. A deterministic source-mode prelude opens
that finding through the public report/continue APIs; the real Codex parent
then runs only corrective Documentation and a fresh Review, which must consume
both immutable refs before resolving it. The evaluator stops after the server
prepares Close, verifies the persisted trace graph, and terminates the entire
parent process group at **300 seconds**; that timeout can only be reduced. It
neither installs nor updates the plugin, and a `SKIP` or timeout is not
evidence of a pass.

```bash
python3 scripts/cortex-luna-high-eval.py --live --scenario finding_rework_documentation
```

`finding_rework_documentation_full` is deliberately a separate C2 live gate.
Unlike the narrow scenario, it has no seeded opening report: the real parent
starts exactly one task and executes the opening Review, corrective
Documentation, fresh resolving Review, and Close. It passes only after the
server records a completed task, a close-bound final handoff, complete current
manifest receipts, documentation/reassessment receipts, and no active gates.
Close reports `findings=[]`, because the fresh Review—not Close—owns the one
canonical finding transition to `resolved`. Its hard cap is **1,800 seconds**;
the timeout flag may reduce but not increase it. This is source-mode evidence
only and neither installs nor updates the plugin. It proves canonical
receipt-bound state plus the observed native worker lifecycle, not trusted
native child-ID/model binding: `--ignore-user-config` intentionally prevents
the installed Cortex hooks from participating. A hook-enabled isolated
integration run is required for that higher-assurance proof.

The full scenario additionally exercises host-level **per-agent MCP
provisioning**: the parent may receive a coordinator-audience endpoint and
each spawned child a worker-audience endpoint. The ordinary static launch uses
the nine-operation compatibility projection and can run the lifecycle; strict
per-agent provisioning is a higher-assurance role-separation check. Do not
merge explicit strict projections merely to make a failed per-agent run appear
to pass.

```bash
python3 scripts/cortex-luna-high-eval.py --live --scenario finding_rework_documentation_full
```

`verify-cortex-release.py --require-tracked` runs only after a commit exists;
it validates `git archive HEAD`, not the mutable worktree.

## Current source-tree evidence

The evidence bullets below describe the previously validated 9.2.4 source
candidate. They do not certify the 9.2.9 hardening release candidate above;
those full-suite, live, archive, and installed-plugin result slots remain
pending.

- The complete source suite passed **550 tests** with 16 intentional native-UI
  skips. Focused governance/migration coverage includes digest-only
  coordinator capability storage and legacy scrubbing, exhaustive `off`
  assessment, canonical independent-review attestation, sensitive-record
  retention/access enforcement, project-scope promotion, approval-ordered
  policy revision, conflicting replay rejection, and corrective-effort
  escalation. Three repeated focused rework runs also passed.
- The black-box cold-boot JSON-RPC smoke passed all 13 dynamically selected
  gates through close, retained implementation across replans, and completed
  three evidence-backed replans despite a persisted legacy limit of two. The
  deterministic evaluator passed all three fixtures. The isolated source-mode
  live `follow_up_partial` task also passed through a real Codex parent in 24
  seconds: it created one linked corrective task, prepared its first dispatch,
  used only the public follow-up management route, made no failed public calls,
  and left source unchanged. The targeted `automatic_governance` live scenario
  then passed through a real `gpt-5.6-luna` high parent in 1,375 seconds. The
  caller supplied C3 complexity and implementation/documentation/close waves
  but no governance mode or trigger fields. Cortex resolved `auto` to `full`,
  inserted and completed `governance_activation` and `governance_close` around
  implementation and documentation, accepted two independent code-reviewer
  results with typed immutable verified obligation evidence, completed close,
  cleaned manifest snapshots, and created the final handoff. All live checks
  passed with no failed public calls and no `manage_governance` forcing.
- Marketplace validation, the isolated fresh-plugin probe, Python and shell
  syntax checks, `git diff --check`, the no-write installer dry run, and the
  8-worker/5-wave call-count benchmark passed. Host preflight and
  `sync-cortex.sh --check` correctly remain blocked because this source is
  9.2.4 while the user-installed plugin is 9.2.3; the installation was not
  changed. Matching installed-plugin verification therefore remains blocked.
  Per the targeted test scope, the default five-scenario live release matrix
  was not rerun; the tracked release archive gate also remains unrun.

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

Ordinary live scenarios have a 1,800-second default timeout, overridable only
from 10 through 7,200 seconds with `--live-timeout-seconds`. The dedicated
`finding_rework_documentation` smoke has a hard 300-second maximum and defaults
to that value; its timeout flag may only reduce the limit. The Codex parent starts
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

The source manifest now declares the 9.2.9 source cachebuster. Historical
9.2.4 results above remain evidence for that prior source candidate only;
release publication and installed-plugin verification remain separate,
explicitly requested actions.

## 9.2.9 release-candidate evidence status

This section describes the hardening work visible in the source tree. The
source cachebuster is generated from the 9.2.9 base version.
The following result slots remain intentionally factual placeholders until the
candidate is committed and rerun on the exact release SHA:

- Full offline regression suite: **pending**.
- Source-mode automatic-governance live lifecycle: **pending**.
- Tracked release archive validation: **pending**.
- Installed-plugin verification and cachebuster parity: **pending; no install
  or user `~/.codex` mutation is implied**.

The draft scope covers governance schema v12 integrity, artifact-authoritative
record bodies, exact scope and linear revisions, append-only status/approval
lifecycle authority, deterministic pre-v10 v9 reconciliation, linked-task
completion and governed-link deletion restrictions, scoped capability claims
with coordinator-audience two-phase recovery delivery/acknowledgement and
fail-closed lost-start handling, no-progress pauses, revision-aware
steer/questions, bounded/cache-backed manifests, and
the required 50,000-file benchmark. A
benchmark pass or focused local check must not be read as evidence for the
pending full-suite or live gates.

## Current 9.2.9 source contract

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
- The host-private `cortex.db` (default
  `~/.codex/cortex/projects/p-<sha256>/`; private outside-workspace
  `CORTEX_HOST_STATE_DIR` override) is the only coordination-state source for
  new tasks. Immutable
  reports, receipts, evidence, briefings, planning artifacts, handoffs, and
  manifests are catalogued in SQLite. Markdown/JSON task files are regenerated
  audit/Desktop projections; altering, deleting, or adding a projection cannot
  restore or modify coordination state. Existing pre-SQLite files are neither
  inspected, imported, resumed, altered, nor deleted.
- Database schema changes are numbered and checksummed through **schema v12**.
  First MCP access takes
  the project-ledger lock and applies only missing SQLite-to-SQLite migrations
  atomically; a failed or mismatched migration fails closed. Checksums cover
  migration version, name, and ordered normalized SQL content. Legacy
  name-only checksums are upgraded only after schema validation. The schema
  uses unbounded SQLite `TEXT`/`BLOB` values for content and indexed 32 KiB
  immutable artifact chunks for transport. Schema v11 added the append-only
  status/approval-basis lifecycle chain; schema v12 hardens the governance
  ledger with host-keyed authentication for the complete lifecycle envelope,
  governed link-deletion restrictions, and terminal linked-task checks. Schema v10
  adds artifact-authoritative bodies, exact non-null scopes, linear revision
  indexes/triggers, and idempotent submission receipts; schema v9
  added the initial governance ledger; schema v8 added task/plan revisions,
  native worker sessions, attempt messages, trace/tool observations, and
  question-batch storage; schema v7 separates deduplicated
  content blobs, task-scoped logical artifacts, and authorized filesystem
  exports; legacy v2 artifact rows are retained as migration evidence.
- Projection work is a durable SQLite outbox. A job is committed with the
  canonical artifact, claimed under a lease, materialized atomically with
  digest verification, then acknowledged in a separate transaction. Required
  briefings are capability files; reports and planning outputs are optional,
  rebuildable projections. Task directories and projection parents are lazy.
- Review, governance activation, governance close, and close reports require a
  canonical top-level structured `gate_result` sibling. The older `closure`
  input remains a compatibility alias for those gates but is not retained in
  canonical report artifacts. Open P0/P1/P2
  or blocking findings, and missing required verification, reopen the recorded
  target gate for rework; resolved or auditable non-self waivers are retained
  in SQLite rather than inferred from prose.
- Report drafts use one private file. `get_report_template` creates a fully
  structured JSON file with mode `0600` and returns `draft_ref`, `draft_path`,
  and expiry without returning the body. Writers edit that exact file;
  read-only workers may send a small RFC 7396 merge patch or complete
  replacement through `record_report`. Invalid records leave the same file in
  place and consume no worker attempt; a new template supersedes an old or
  expired draft. `record_report` rereads/revalidates current state, atomically
  persists, then deletes the file and metadata only after commit. Legacy
  full-payload `record_report` remains compatible. Host-sandboxed read-only
  gates record ordinary shared-checkout source deltas as concurrency evidence;
  claimed `changed_files` still fail. Every ignored side effect is retained as
  non-blocking digest evidence, with recognized conventional test/build/cache
  residue and unknown framework output classified separately.
- The server-owned `resolved_user_decisions` snapshot is carried with each
  immutable report outside the worker-authored seven-field envelope. Report
  metadata and Markdown expose its count/digest and decision sources, while
  replacement briefings receive a bounded recent projection. Choice batch
  answers preserve stable option IDs and optional custom context; localized
  custom context requires canonical English before worker resumption.
- Report-publication regression coverage binds the only user-facing Markdown
  link to the matching durable native-worker stop. It proves that an early
  read is link-free, the first eligible complete coordinator read returns
  `publication_required=true` with a completion summary and next step, and a
  reread cannot publish the link again.
- Worker caller/input/schema validation results are structured corrections:
  fix the named field and retry the same tool on the same attempt without
  creating a failed worker outcome or changing escalation counters. `get_report_template` and
  `worker_question` return correction envelopes. Briefing, predecessor-report,
  and coordinator artifact reads clamp oversized `max_bytes` to 32768. Only
  explicit `retryable: false` integrity, storage, permission, or unavailable
  identity failures are terminal.
- Active user corrections use a task revision and resume addressable native
  worker sessions in place; completed-task corrections remain linked
  `follow_up` tasks. Schema v8 stores the revision, session, and atomic
  question-batch identity; `ask_batch`/`poll_batch` cover 1–32 questions with
  canonical answers. Main chat keeps one durable batch ref, renders every
  question with its required LLM recommendation, checkpoints accepted answers,
  and resumes only after the next user message is recorded.
- Revision-aware steer classifies the earliest affected gate and invalidates
  downstream evidence. Questions carry task/plan revision and strategy
  generation, so unresolved questions from an older revision are superseded
  and cannot accept stale answers. Corrective work has no fixed attempt quota,
  but a repeated materially identical no-progress signature pauses autonomous
  retries for an explicit user strategy without producing a false pass. The
  recovery tests require a singleton Planner-first wave and a material change
  to pipeline, strategy, or verification, or a class-matched environment
  remediation; reason prose is audit-only.
- Manifest capture enforces `max_entries`, `max_hashed_bytes`, and
  `max_seconds`; a limit produces a partial result with a reason, but a
  partial baseline/current capture cannot authorize mutation reconciliation,
  handoff, or terminal close. Unchanged file hashes may use a bounded
  stat-keyed cache. CI additionally requires
  `python3 -B scripts/cortex-manifest-benchmark.py --files 50000
  --max-seconds 30` to report `target_met: true`.
- Required post-plan review is surfaced through one ordinary final assistant
  message using `cortex/chat-interaction/v1`. It includes the complete bounded
  plan, approve/revise/cancel meanings, opaque request ID, and LLM recommendation.
  Tests assert that no nested UI request is emitted, the turn pauses, approve
  dispatches the next wave, revision feedback is preserved, cancel remains
  pending, and stale or replayed responses do not dispatch.
- Prune first commits a tombstone, removes the projection tree outside the
  state lock, and only then deletes canonical task rows in one final SQLite
  transaction. Failed filesystem removal leaves the task recoverable for a
  retry. Legacy v7/v3 files are never imported into the active ledger; their
  explicit maintenance route inventories, archives, and only then permits
  confirmation-bound deletion.
- `manage_orchestration(intent="maintenance")` provides read-only SQLite
  health inspection plus confirmation-bound checkpoint, private
  `.cortex-backup` disaster-recovery bundle creation, fresh-host
  bundle-restore verification, optimize, vacuum, and projection reconciliation
  operations. A bundle atomically contains the SQLite ledger, the v12
  governance lifecycle host key, and a fingerprint manifest; verification
  checks SQLite integrity and reads every governance record through its real
  lifecycle-authentication layer. It never treats WAL/SHM sidecars as
  application artifacts. A bare historical `.sqlite` copy is not advertised
  as a recoverable Cortex backup.
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
close evidence. They also cover the stranded completion-pending stop contract:
`report_recorded` attempts with `host_report_refs` are not active or resumable;
continuation requires one explicitly selected report ref bound to the exact
task/gate/attempt/revision, while auto-selection, implicit approval, respawn,
and invalid, stale, consumed, or mismatched refs fail closed into recovery.
Multiple valid refs remain selectable audit evidence. They also cover
human-readable `Profile Module` labels and
attempt-unique native `task_name` values, so a host cannot resume a stale child
under a repeated display label.

Public API tests require a nine-operation MCP registry projected as exactly
five tools per explicit launch-time audience. The coordinator projection contains
`start_orchestration`, `continue_orchestration`, `manage_orchestration`,
coordinator-only `manage_governance`, and scoped `read_worker_report`; the
worker projection contains `worker_question`, `get_report_template`,
`record_report`, identity/digest-scoped `read_dispatch_briefing`, and scoped
`read_worker_report`. Native worker prompts carry a compact bootstrap with the
exact immutable briefing path/digest; the worker cannot enumerate the ledger.

The automatic pipeline tests enforce the model-routing contract: Explorer uses
Luna, high-risk Security uses Sol at its complexity floor, and other profiles
select Luna or Terra according to their machine-readable profile policy. They
also validate the plan-approval boundary: ordinary user tasks may require it,
while harvest is automatic after its source-backed plan.

Prompt-architecture regressions validate explicit Cortex opt-in, the
`cortex-control` runtime-core handoff, conditional harvest overlays, and the
Worker Briefing v3 budget-enforced assignment envelope. Adversarial task values are serialized
and round-tripped as JSON, rather than interpreted as prompt structure; tests
also reject long duplicate prompt paragraphs and enforce representative
bootstrap/briefing budgets (1,500 bytes; ordinary 16/24 KiB soft/hard;
harvest 18/28 KiB soft/hard). The ordinary values occupy the recommended
14–16 KiB soft and 20–24 KiB hard envelope; the harvest overlay uses the
expanded 16–18 KiB soft and 24–28 KiB hard envelope. Retry regressions enforce
unbounded pipeline rework, the `high`/`xhigh`/`max` effort escalation, Terra
routing after two unresolved attempts, and optional evidence-backed
`next_strategy` or pipeline replanning.

Predecessor regressions retain the complete Planner evidence basis while
dispatching only the verified transitive frontier, exercise a 33-report
pre-plan history without a count blocker, and reduce a synthetic chain of more
than 1,000 durably acknowledged reports to its current frontier.

The release boundary rejects nested marketplaces, runtime state, bytecode,
symlinks, unsafe paths, private home paths, credential-like files, and missing
policy material from the archive. Structured public validation failures are not
exception-log events; actual MCP exceptions are redacted and correlate only
bounded identifiers.

<!-- GENERATED:START -->

## Authoritative command inventory

- `python3 -m unittest discover -s tests -v` — standard-library regression suite; CI source: [cortex.yml](../../.github/workflows/cortex.yml).
- The [Cortex offline-validation workflow](../../.github/workflows/cortex.yml) runs this suite on Python 3.11 and 3.12 with `PYTHONDONTWRITEBYTECODE=1`, `PYTHONUNBUFFERED=1`, and `PYTHONHASHSEED=0`; each command also uses `python -B` so hosted runs do not create bytecode or depend on hash iteration order.
- The workflow runs the reportless plan-stop regression in an isolated step before the aggregate suite. Reproduce that gate locally with `PYTHONDONTWRITEBYTECODE=1 PYTHONHASHSEED=0 python3 -B -W error::ResourceWarning -m unittest -v tests.test_revision_aware_epic.RevisionAwareEpicAcceptanceTests.test_reportless_plan_stop_requires_failed_receipt_before_retry`.
- `python3 scripts/cortex-cold-boot-smoke.py` — black-box JSON-RPC lifecycle smoke test; CI source: [cortex.yml](../../.github/workflows/cortex.yml).
- `python3 scripts/cortex-luna-high-eval.py` — deterministic Luna-high fixtures; add `--live --scenario automatic_sequential` for the ordinary lifecycle, `--live --scenario automatic_governance` for the C3 auto-governance lifecycle, `--live --scenario finding_rework_documentation` for the narrow seeded-origin finding route, or `--live --scenario finding_rework_documentation_full` for the complete real C2 finding lifecycle through Close and handoff. The full finding route can run on the nine-operation compatibility projection; host-provisioned per-agent audiences additionally prove strict role separation (coordinator for the parent, worker for every child). It neither installs nor verifies an installed plugin. `--live-timeout-seconds` accepts 10..7200 seconds and defaults to 1800; the narrow finding scenario is capped at 300 seconds, and the full finding scenario is capped at 1800 seconds. `--retain-failure-metadata` explicitly opts into bounded sanitized `/tmp` metadata. `SKIP` is not live evidence.
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
