# Release readiness

This document records the repository-side gates for a public Cortex release.
It does not claim that a commit, tag, remote, catalog submission, or catalog
approval exists.

## 9.2.19 release candidate

This is a source-tree hardening candidate, not a published release. Its source
cachebuster is generated from the 9.2.19 base version. Full-suite, live-governance,
tracked archive, and installed-plugin results are recorded separately; no
plugin installation or user `~/.codex` mutation is implied by this section.

The 9.2.19 patch keeps programmatic dispatch-briefing size rejection removed and
keeps complete Planner content in digest-bound immutable artifacts. It also
makes no-progress recovery gate-local: an unpaused current-wave sibling can
finish, later dependency waves do not leapfrog a pause, and multi-pause
Planner-first recovery names the exact gate to release. Report preparation now
runs outside the final commit, with task-revision, draft-identity, and
content-digest CAS. Lock acquisition is bounded to five seconds; once acquired,
the canonical report/artifact commit remains serialized until its writes finish.
`ledger_busy` and `stale_preparation` are retryable and preserve the draft. The
`CORTEX_REPORT_PREPARE_COMMIT=off` rollback flag retains the serialized legacy
path.

All other ledger mutation callers use the same bounded lock default. A busy
response contains only operation, duration, and non-secret holder metadata;
expected contention is not appended to the private tool-error log.

Lifecycle hooks use an existing SQLite read-only snapshot and never run
migrations or state locks. Optional telemetry writes use a 100 ms busy timeout
and fail open; missing production telemetry does not change acceptance.

The retained 9.2.17 patch makes duplicate full reads of an unchanged bundled
Cortex skill advisory and context-aware: the read remains allowed, compaction
or a new context epoch permits it again, and host-internal UI skill loads
remain outside the project-tool hook boundary.

The 9.2.16 patch additionally ensures coordinator recovery does not ask a
reviewer to retry an impossible resolution report, preserves honest `BLOCKED`
markers through review and close while corrective work remains, and aligns the
`record_report` schema branch with runtime validation.
It also removes the former 64 KiB/100-item report sanitization caps: complete
immutable reports use the explicit 8 MiB atomic artifact boundary, private
report drafts allow 17 MiB for envelope headroom, and cursor reads expose the
full artifact while limiting only each transport page to 32 KiB. Detailed
plans are dispatched by exact immutable artifact ref/path/SHA-256 metadata in
the briefing.

The draft scope is governance schema v12 integrity (artifact-authoritative
bodies, exact scope, linear revisions, strict JSON, immutable-field triggers,
idempotent submissions, append-only status/approval-basis lifecycle authority,
deterministic pre-v10 v9 reconciliation, linked milestone/deliverable success,
and governed-link deletion restrictions), scoped capability claims with
coordinator-audience same-identity two-phase recovery delivery and explicit
acknowledgement before revocation,
no-progress pause semantics, revision-aware steer and questions,
bounded/cache-backed manifest capture, the 50,000-file benchmark, and
CI/CODEOWNERS release evidence.

It also repairs host-stopped report recovery: `report_recorded` is an explicit
completion-pending state, not a live child. A coordinator selects one
receipt-attested report; a stale Planner report cannot mutate the plan, and no
eligible report transitions the task only to a fresh Planner-first recovery.

The candidate also keeps the worker/coordinator report boundary strict:
`task_ref`, `dispatch_ref`, and `submission_id` are coordinator transport
fields, never `record_report` identity. Worker drafts are verified on the
opened descriptor as current-user regular non-symlink files with exact `0600`
mode; a rejected direct edit is not path-repaired. Evidence-marker diagnostics
name the exact required marker and criterion, while `changed_files` remains
bound to the immutable baseline of that exact worker attempt. Lifecycle hooks
resolve their bundled runtime when a host loads them through `importlib`.
An active closure-rework route also preserves its exact server-bound corrective
receipt through intermediate QA/security acknowledgements, so the originating
verifier can receive both the source finding and correction before issuing a
resolution receipt. `required_missing=[]` is scoped to the publishing gate and
does not synthesize a resolved transition for a server-created verification
finding from another gate. Governance-origin rework moves its fresh origin
gate and every later closure verifier behind the corrective target, preserving
the required resolution provenance without weakening the blocker. Before an
origin verifier is dispatched, every active closure-rework finding/origin
binding must have a current passed, server-bound corrective receipt; otherwise
the controller returns recoverable `closure_rework_preflight_required` before
creating a worker or report draft. This keeps a reviewer from being asked to
write a resolution report that its permitted context cannot prove.
Malformed caller JSON at the public report boundary is converted into a bounded
same-attempt validation result rather than an interpreter exception. An
explicitly external `codex://threads/...` ledger-continuation task without a
requested project mutation omits write-required implementation and QA gates;
any explicit repository mutation preserves the standard implementation route.

Worker Briefing v3 compaction targets remain prompt guidance: the native
bootstrap should stay compact, ordinary briefings target 16 KiB, and harvest
briefings target 18 KiB. They are not programmatic report-acceptance or
dispatch-rejection budgets. Complete Planner detail is retained in immutable
digest-bound artifacts and workers receive their exact ref/path/SHA-256 rather
than a duplicated inline plan.

## Package contract

- `.agents/plugins/marketplace.json` is the only marketplace manifest.
- `plugins/cortex` is the only installable Cortex source tree.
- Root development scripts, tests, and documentation support the package but
  are not duplicate installable agent or skill sources.
- The plugin and MCP server versions must match the release contract
  `9.2.19` (the current source candidate carries a cachebuster; installed builds
  may carry a different cachebuster).
- Runtime selection is fail-closed: set `CORTEX_PYTHON` to one absolute
  executable path for Python 3.11+ with `tomllib`, or leave it unset to resolve
  `python3` from `PATH`. The installer, MCP server, and lifecycle hooks use the
  same selection, and an invalid explicit path must not mutate Codex
  configuration.
- Optional public manifest fields are not added until their exact names and
  shapes are verified against the installed or official Codex schema. The
  current release work does not invent repository, homepage, license, privacy,
  screenshot, or prompt-array metadata.

## Trust boundaries

The tracked release is built from `git archive HEAD`, never from mutable local
runtime state. Validation rejects symlinks, hard links, path traversal, the
retired nested marketplace, `.codex` state, bytecode, secret-prone filenames
and credential-store paths, missing public policy documents, private local
home paths in public files, and explicit release placeholders.

Runtime coordination state is host-private and defaults to
`~/.codex/cortex/projects/p-<sha256>/`; a private, outside-workspace
`CORTEX_HOST_STATE_DIR` is the only host override. Legacy project-local
`.codex/cortex` databases move only through validated same-filesystem atomic
rename, and unsafe, split, non-database, or cross-filesystem legacy state fails
closed.

The installer validates `HOME` and `CODEX_HOME` ancestry, preserves the user
MCP approval override, and creates a collision-safe private backup only before
changing a configured global default-subagent model. It never inspects or
removes previous orchestration state or unrelated plugin files.

Governance schema v12 makes immutable content artifacts authoritative, enforces
exact normalized scope and linear revisions, and fails closed on strict-JSON or
immutable-field violations. Its append-only lifecycle chain authorizes status
and approval basis; deterministic v9 conflicts are reconciled before v10
indexes, while ambiguous graphs fail closed. Schema v12 additionally
authenticates the complete lifecycle event envelope with a host-private key
outside SQLite. Linked milestone/deliverable
tasks must be terminally successful for initiative completion, and governed
initiative-task links cannot be deleted. Coordinator capabilities are
short-lived claims bound to task/initiative, principal, thread, generation,
expiry, and allowed actions; same-identity recovery is available only on the
explicit coordinator audience and requires the non-durable proof returned with
authorization, rotating and revoking generations without storing plaintext
bearers or proofs. Corrective work has no fixed attempt quota, but a materially
identical no-progress signature pauses autonomous retries for an explicit user
strategy. Recovery is Planner-first and must materially change pipeline,
strategy, or verification, or name a matching environment remediation; reason
prose is audit-only. Material steer classifies the earliest affected gate;
questions are bound to task/plan revision and strategy generation.

Manifest capture is bounded by entries, hashed bytes, and elapsed time, with a
bounded digest cache. Partial captures remain diagnostic only and cannot
authorize mutation reconciliation, handoff, or terminal close. CI requires the 50,000-file manifest benchmark to report
`target_met: true`; the workflow keeps Python 3.11/3.12, a 30-minute timeout,
per-ref cancellation, and the existing release gates.

Report finalization uses a private draft file: `get_report_template`
creates a fully structured JSON file with mode `0600` and returns `draft_ref`,
`draft_path`, and expiry without returning the body. Writers edit that exact
file; read-only workers may send a small RFC 7396 merge patch or complete
replacement through `record_report`. Invalid `record_report` calls leave the
same file in place and consume no attempt. A new template
supersedes an old or expired draft. `record_report` rereads/revalidates and
deletes the file and metadata only after commit. Normal callers send only
worker identity and ref/payload; `task_ref`, `dispatch_ref`, and
`submission_id` are rejected. The current-user regular non-symlink draft must
remain exact `0600`; rejection leaves a caller-authored replacement untouched.
Legacy full-payload recording remains compatible.
The server-owned `resolved_user_decisions` projection is attached outside the
worker-authored seven-field report, with bounded recent copies in replacement
briefings; localized choice custom context is retained and translated to
canonical English before resumption.
Host-sandboxed read-only gates treat ordinary shared-checkout source deltas as
concurrency evidence. Recognized cross-language test, build, and cache residue
is retained in the audit receipt; claimed changes, unknown artifacts, and
arbitrary `.gitignore` outputs remain failures.
Worker input/schema validation is retryable on the same attempt and creates no
failed worker outcome. Failed work remains durable escalation evidence but has
no pipeline or same-strategy attempt limit: effort rises through `high`,
`xhigh`, and `max`, with Terra selected for eligible ordinary work after two
prior failures. A materially identical no-progress signature is the separate
liveness boundary: it pauses autonomous work and requires an explicit new
strategy without synthesizing a pass. A different strategy is optional for
ordinary retries, but required to resume that pause.
Bounded briefing and coordinator artifact reads clamp oversized `max_bytes` to
32768; report reads use the same transport-page bound while returning the
complete immutable artifact through cursors. Explicit non-retryable
integrity, storage, permission, or unavailable-identity failures remain
terminal.

## Required repository checks

Run the commands in `docs/project/verification.md`. A release candidate must
pass the full regression suite, marketplace validation, Python and shell syntax
checks, cold-boot smoke test, isolated fresh-plugin probe, and the blocking
tracked-release archive validation. The CI manifest gate must also run
`python3 -B scripts/cortex-manifest-benchmark.py --files 50000 --max-seconds
30` and reject any result without `target_met: true`.

The read-only host gate is separate from source and archive evidence:
`cortex-host-preflight.py --json` must report `mcp.status=READY` only for the
same Codex user with a matching enabled `cortex@cortex` registration, approval
configuration, cache-backed
hook trust, and all other prerequisite checks.
The named `Hetzner_Bots` host remains blocked until an approved Node >=16
installation source is available; no guessed package-source command is a
release step. Follow [SSH host troubleshooting](project/ssh-hetzner-troubleshooting.md)
for the safe same-user sequence and the bounded stopped-worker recovery.

The 9.2.4 source candidate passed the complete 550-test regression suite (16 intentional
native-UI skips), focused governance/migration and repeated rework escalation
regressions, cold-boot smoke, deterministic fixtures, marketplace validation,
the isolated source-mode live `follow_up_partial` task, the isolated
fresh-plugin probe, syntax/diff checks, no-write installer dry
run, the composite benchmark, and the source-mode live `automatic_governance`
C3 lifecycle. That live scenario omitted governance mode/trigger inputs,
resolved `auto` to `full`, completed the server-added activation and close
reviews plus implementation/documentation/close, and passed every evidence,
cleanup, handoff, and no-forcing check. Matching installed-plugin verification
remains blocked until an explicitly authorized update. The default
five-scenario live release matrix was not rerun after this fix, and the tracked
release archive gate remains a separate release check. Host preflight correctly
reports the user's still-installed 9.2.3 copy as stale relative to this 9.2.4
source; no installation or `~/.codex` mutation is part of this candidate.
The evidence-first pipeline,
scope artifact, plan-basis digests, v1 resume compatibility, 10 MiB
tail-preserving error-log cap, and Bash 3.2 launcher compatibility require
focused regression coverage. The 9.2.4 ledger starts from SQLite only: its
checksummed migrations operate SQLite-to-SQLite, while pre-SQLite task files
are left untouched and never become coordination state. Installation preserves
the user MCP approval override. Targeted development validation, full
lifecycle live-model validation, and tracked-release validation are split
deliberately; the remaining release results and the post-commit archive result
are recorded in `docs/project/verification.md` before push.
Tag, catalog submission, approval, and public publication are not part of this
local plugin update and are not claimed.

## External release gates

- Create the Cortex 9.2.19 release commit only with explicit authorization.
- Rerun `python3 scripts/verify-cortex-release.py --require-tracked` against the
  real committed tree; an unborn `HEAD` is a release blocker.
- Verify any optional public manifest metadata against the current official or
  installed Codex schema before adding it.
- Configure and verify a confidential security reporting route without placing
  personal contact data in the repository.
- Verify a clean installation from the immutable Git tag in fresh `HOME` and
  `CODEX_HOME` directories.
- Establish the authorized public remote provenance, then review `git ls-files`
  and the final archive inventory before push, tag, or catalog submission.
- Obtain the catalog's required approval or authorization; local marketplace
  registration and an isolated CLI probe do not establish catalog availability.

Failed external gates block publication. Existing tags are never rewritten to
repair a release; publish a new cachebusted version with a changelog entry.
