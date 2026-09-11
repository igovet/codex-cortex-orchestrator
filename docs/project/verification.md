# Verification

## Stable release payload — 1.15.9

The current Cortex semantic version is `1.15.9`, with stamped payload
`1.15.9+codex.sha256.e4f332d43bf38024` as declared by the plugin manifest and
resolved by the package validator. Final consecutive real CLI and Desktop
qualification passed on this unchanged candidate (`r_648c4f40dcf9`). Combined
with the offline tests and independent reviews, this supports stable,
release-ready orchestrator functionality. The user cancelled the Phase 2
outcome comparison; it is non-blocking and non-scoring, and no efficacy
comparison is claimed.

## Current 1.15.9 stable release status

| Gate or evidence | Status | Exact retained evidence |
| --- | --- | --- |
| Final consecutive real CLI qualification | PASS | `r_648c4f40dcf9`; unchanged candidate; classified audit clean on CLI |
| Final consecutive real Desktop qualification | PASS | `r_648c4f40dcf9`; unchanged candidate; classified audit clean on Desktop |
| Phase 2 outcome evaluation | CANCELLED — non-blocking, non-scoring | User-directed cancellation; no efficacy comparison, score, promotion, or quality conclusion is claimed |
| Phase 3 validator repairs | READY offline | `r_02db5d25f90e` — final adversarial review PASS |
| Phase 4 telemetry repairs | READY offline | `r_6b761e716df9` — independent re-review READY |
| Phase 5 adaptive overlay | READY offline | `r_e95d3d29d46f` — final adversarial re-review PASS |

Both final host runs used isolated preparation, Luna coordination/workers, no
Astra, and no stable installation or configuration change. Their audits reported
zero failures, policy violations, open sessions/cells, integrity invalidators,
or quality findings. Phase 2 is cancelled by the user and must not be restarted
or interpreted as an efficacy comparison; the Phase 3–5 repairs are READY
offline as recorded above.

Earlier excluded live attempts and constrained control/preflight checks remain
historical evidence only. They are not relabeled, merged, retried, scored, or
used as substitutes for the current qualification. Superseded candidate
identities and earlier failures remain historical diagnostics only.

## Coordinator draft-publication guidance repair — 2026-09-11

The coordinator skill and pipeline-publication reference now require treating the
`create_draft` Markdown and ordered `replaceable_markers` as authoritative,
replacing every exact marker before `write_report`, and matching document kind to
ownership (`pipeline` edition for the coordinator versus non-pipeline report for a
worker). A deterministic `draft_guidance_remaining` response keeps the same draft,
request key and metadata for one in-place correction retry; acknowledged
publications are never replayed. This is model guidance only: no server-side
approval machine or acceptance semantics were added. Focused skill assertions and
skill validation passed; no live CLI/Desktop run was performed for this repair.

## Desktop activation fallback repair — 2026-09-10

The Desktop helper now preserves strict activation for successful
`windowactivate --sync` calls. A fallback is permitted only when the command
returns the exact `XGetWindowProperty[_NET_WM_DESKTOP] failed (code=1)` warning.
It records that bounded diagnostic, rechecks the launched PID, runs one
`windowfocus --sync`, requires `getactivewindow` to identify the same window, and
rechecks the PID before click or `Ctrl+Enter`. Different xdotool errors, failed
focus, active-window mismatch, or ownership change fail closed. The existing
prepared-composer and exactly-one-new-task-receipt gates are unchanged.

Focused regressions cover normal activation, exact-warning fallback success,
other X11 errors, and PID ownership change during fallback. Package, sync, full
test, and real-host qualification results for this repair must be recorded below;
no live Desktop run is claimed by this source change alone.

## Desktop spawn provenance and newline skill-read repair — 2026-09-10

The isolated Desktop launcher now requires every `spawn_agent` request to record
the child `model`, `reasoning_effort`, and `fork_turns` explicitly. The observer
continues to require matching native child model/effort, parent linkage, and
complete skill evidence; missing requested fields remain unverified. Focused
regressions cover both an explicit Luna/effort route and an omitted-field failure.

The command-aware observer parser preserves literal newlines outside quotes as
linear boundaries, allowing a bounded batch of approved `sed` skill/reference
reads. Newlines inside quotes, escaped newlines, mixed approved/private targets,
and direct or ambiguous private-cache access remain fail-closed. Focused positive
and negative regressions cover these boundaries. No live CLI or Desktop run was
performed for this source repair.

## Opaque Desktop route fallback repair — 2026-09-10

The observer now accepts effective Luna worker-route compliance when an encrypted
native assignment makes both requested model and effort fields unavailable and
the exact `assignment_content_unavailable=true` marker is present, but only with
a unique native child path, validated parent edge, registered executor
profile, actual `gpt-5.6-luna` model, allowed medium/high effort, `fork_turns=none`,
and one successful complete worker-skill receipt. It records bounded actual-route
provenance without reconstructing or hashing hidden assignment prose. A visible
requested field retains strict requested/observed matching; missing, partial,
conflicting, duplicate, incomplete, failed, or ambiguous evidence remains
`worker_assignment_policy_unverified`. Retained command errors, pending waits,
MCP failures, and content-safety findings remain independent acceptance failures.

Native `SubAgentActivity` evidence is lifecycle-aware: one `started` and one
`completed` record for the same child/path coalesce into one route identity.
Repeated starts, repeated completions, or a path resolving to different children
remain ambiguous and fail closed; parent-edge, model/effort, and complete-skill
receipt conflicts retain the same strict rejection behavior.

Focused observer regressions cover the positive unavailable-fields join and
negative route, receipt, profile, model, effort, fork, partial-request, lifecycle,
and duplicate-evidence cases. No live CLI or Desktop run was performed for this
source-only observer repair.

## Historical 1.15.9 candidate (superseded): Permission-safe worker checks and post-wait sequencing — 2026-09-09

The superseded historical payload was `1.15.9+codex.sha256.3b0e71f080f852ff`.
Its implementation evidence records 439 offline tests passed and 2 skipped after the final stamp;
package validation, marketplace validation, and source-only sync also passed.

That historical repair added two independent protections for disposable live runs:
worker guidance prefixes Python checks with `PYTHONDONTWRITEBYTECODE=1` and avoids
recursive cache cleanup, while both isolated launchers force that environment value
even when the ambient value is `0`. Pre-existing residue remains untouched and a
blocking check must report its exact authority requirement; host permissions and
`scripts/cortex-dev` preparation are unchanged.

Coordinator guidance now treats pending/timeout `wait_agent` results as absence of
new evidence and requires another bounded wait for the same owner. Internal status
and selected evidence reads remain legal; unsolicited `send_message`/`followup_task`
remain audit violations. Legal transitions are limited to an inbound same-owner
reply, direct user steering/clarification, or intentional follow-up after terminal
worker result/report reconciliation. This is model guidance, not a server gate.

Focused offline bytecode and sequencing regressions were part of that historical release's checks.
Real CLI/Desktop qualification, consecutive parity, matrix/scoring, and
model-level compliance are intentionally unrun and remain pending; no current live
qualification claim is established by this implementation assignment.

## Observer provenance boundary — 2026-09-10

The live observer now keeps ordinary external worker tooling separate from
orchestration acceptance. Codebase Memory calls, Git/rg nonzero diagnostics and
ordinary workspace path observations remain visible worker evidence; they do not
become orchestration failures from tool identity or exit status alone. Path-policy
rows carry only safe provenance, target class, access kind and decision metadata,
plus bounded argument/workdir and command-intent digests, terminal exit status and
truncation state when unambiguous. Violations retain those hashes and actor/thread
lineage without retaining private paths, commands or report content.
MCP error and missing-receipt policy flags are likewise scoped to actual
`mcp__cortex__*` invocations; a failed, properly receipted external MCP provider
call remains a worker diagnostic. Namespaced host rows are canonicalized while
retaining `tool_namespace` in violation evidence, so a Cortex receipt failure
cannot disappear during audit promotion.
An explicit unauthorized plugin/cache/Cortex target remains acceptance-critical,
while static mentions and approved skill reads remain allowed. Focused regressions
cover ordinary Codebase Memory, Git exit 128, a non-prohibited worker path, an
approved instruction read, command-aware `rg`/`grep` pattern-vs-target-vs-ambiguous
classification, and a genuine unauthorized cache target. No live CLI or
Desktop scenario was run for this repair; those gates remain coordinator-owned.

## Private-root preflight provenance repair — 2026-09-10

The deny-only worker guard now accepts a missing `agent_id` only when the hook
payload supplies a matching child thread and parent session that the durable
task binding validates. Session-scoped coordinator/system events without that
provenance remain unblocked. Running worker shell receipts retain a task-scoped
command-session ID and effective work directory; a later `write_stdin` event can
recover that worker scope and directory even when its event omits `agent_id`.
The command-session table is created idempotently for existing v11 stores.

Focused positive and negative regressions cover validated parent/child provenance,
untrusted parent claims, coordinator compatibility, private stateful
`write_stdin`, and unknown command sessions. No live CLI or Desktop scenario was
run; those gates remain coordinator-owned.

## Historical 1.15.9 candidate (superseded): Phase 0–1 evaluation contract and guidance — 2026-09-09

The historical release source and generated payload were versioned `1.15.9`, stamped as
`1.15.9+codex.sha256.6d67370022e39830` (digest
`6d67370022e398307af4e7fe4a06cf85c47ca2c4aaa42401baa3377909df65bf`). The
new offline contract at
[`tests/fixtures/phase01_eval/contract-v1.json`](../../tests/fixtures/phase01_eval/contract-v1.json)
keeps the historical six-configuration comparator intact and adds three held-out
families with three paired baseline/candidate repeats. Blind scoring, invariant
and protocol stops, token/wall/tool cost fields, explicit unavailable/null
handling, and the coordinator-owned promotion/revision rule are covered by
`tests/test_phase01_eval_contract.py`.

Optional context-selected guidance is present in the coordinator and shared
worker instructions only: fresh evidence plus unrun checks for completion claims,
fact/hypothesis separation plus one discriminating check before nontrivial repair,
and an independence/mutation-surface/resource/dependency/output declaration before
parallel dispatch. It remains advisory; no server gate, mandatory stage, or
automatic acceptance behavior was added.

Sequential source checks passed: package validation, marketplace validation,
source-only sync check, focused tests (95 passed), and the complete suite (402
passed, 2 skipped). The coordinator overlay adds risk-triggered consultation:
one bounded advisory consultation is considered only when both consequence and
uncertainty triggers apply, with explicit exclusions, compact packet/record fields,
and baseline quality/overhead evaluation. It does not create a gate or transfer
planning, steering, evidence, acceptance or communication ownership.

Fresh consecutive real-host qualification was reported for that unchanged
historical payload on both CLI and Desktop. CLI produced `notes.md` with exactly
36 bytes, final byte `0a`,
and SHA-256 `b366b4a09a694e27cd0bc93a2ec11b1a2a7ef1657761af45c4fbf5342b67e733`,
plus immutable worker report `r_d76979ae22c9`. Its 10 MCP events and 62 host
calls had no open sessions, policy violations, or orchestration failures; one
worker shell check exited 1 and was later resolved by an exact successful check,
and is retained in the audit evidence. Desktop produced the identical artifact
and worker report `r_fe9198033a67`; its 12 MCP events and 66 host calls had a
fully clean audit (zero failures, policy violations, and open resources), with
1,111,747 total tokens in 168.746s. Both coordinators used Luna/high and workers
Luna/medium. The Desktop send helper reported one X11 desktop-property warning
but exited zero and observed the exact new-task receipt. Both disposable sessions
were stopped; post-stop Desktop status correctly reported no active session.
CLI token usage was not retained before its observation streams were removed.
Stable installation and user tasks were not modified. The three-family Phase-0
outcome suite remains unrun. These historical host results do not qualify either
the superseded `1.15.9+codex.sha256.3b0e71f080f852ff` payload or the current
`1.15.9+codex.sha256.620f15774f61a921` payload. At that historical snapshot,
current CLI/Desktop runs remained pending; the later broad results are recorded
above.

## Clean CLI/Desktop audit pair — 2026-09-09

Candidate `1.15.8+codex.sha256.d3b300414bf3198d` completed consecutive real CLI
and Desktop document scenarios on the same unchanged isolated payload and
dependency identity. Both used Luna high coordinators and Luna medium native
authors without inherited coordinator conversation. The ordinary request asked
for a task-service lifecycle specification with invariants and conflicting
requests, author delegation, and coordinator review.

| Host | Observed calls | MCP events | Final audit | Completed automatic compaction (2026-09-08 UTC) |
| --- | ---: | ---: | --- | --- |
| CLI | 84 | 14 | Exit 0; all failure and policy lists empty | 20:55:29.983 |
| Desktop | 86 | 15 | Exit 0; all failure and policy lists empty | 21:00:04.306, 21:04:14.539, 21:05:08.926 |

Each listed compaction has a nonempty native server response ID. The isolated
configuration lowered the automatic threshold to 40000; no manual compact or
context-reset action was sent. Gateway metadata recorded the V2 high-to-medium
routing, and both tasks continued to native final answers. The test threshold
was removed afterward. CLI exited 0 before its exact tmux session was removed;
Desktop's exact isolated process and disposable profile were stopped after its
completed-task receipt. The stable installation and active user tasks were not
modified.

The changes address broad catalogue truncation, name-only discovery mistaken for
full declarations, guessed/missing arguments, recovery reads losing exit receipts,
and inherited conversation in new assignments. The publication reference no
longer suggests that an initial writer key can be omitted; the writer property
explicitly requires it on the initial call. Permanent draft identity metadata is
distinguished from replaceable guidance. Encrypted assignments retain their
opaque marker; fixed isolated worker routing is verified from actual child
linkage, model/effort and complete skill-loading evidence, without claiming to
inspect hidden prose.

Source verification passed sequentially: package validation, source-only sync
check, and all 388 tests (eight existing aiohttp warnings, no skips). The final
focused package/thread tests passed 44 cases first. Generated instructions retain
all 22 executor profiles and the separate consultant profile within the existing
prompt budgets.

Earlier attempts were rejected for truncated catalogue output, missing or invalid
arguments, incomplete command receipts, inherited worker context, and a false
positive marker check. One earlier CLI run of this same final candidate had a
patch-context error, corrected it, and remained rejected by the audit; the clean
pair above was a fresh run, not a relabelled recovery. Complete current calls and
MCP event tails plus audits were captured before rejected clients were stopped.
No failed outcome was suppressed to obtain the passing pair.

This is bounded evidence for the observed document workflow, not a claim that
all future model runs are error-free. Both final deliveries used coordinator
review of the author's report; no independent reviewer was required by the test
request. CLI published an accepted pipeline edition. Desktop delivered native
final acceptance with its initial pipeline and immutable author report; this
probe does not certify that every final acceptance is mirrored in a new pipeline
edition. Cross-model routing, long native-residency pressure, resume and broader
project workloads were not rerun for this candidate. The earlier summary-routing
probe below remains historical evidence with its original failures intact.

## Automatic summary compaction mode — 2026-09-08

Candidate `1.15.8+codex.sha256.95d66b6c01eaf5ed` selects
`features.context_management = false` and `features.remote_compaction_v2 = true`
through automatic provider connection. The global route journal records prior
feature values, recovers interrupted publication, and preserves subsequent user
overrides. README installation/update instructions now require MCP initialization,
inspection of the effective global configuration, and fresh routing evidence.

Source verification: package validation and source-only sync succeeded; all
387 tests passed with eight existing aiohttp warnings. Focused gateway/provider
tests first passed 107 cases with two missing-zstandard skips in the system
Python; the complete dependency-equipped suite had no skips. The new tests cover
absent and existing feature keys, repeated connection/listener changes, later
user choices, restoration, and interrupted config publication.

Real CLI and Desktop used the same unchanged isolated candidate and dependency
identity, Luna high coordinators, and Luna medium/high workers. Only the isolated
test configuration lowered `model_auto_compact_token_limit` to 40000. Ordinary
document work grew the context; no `/compact` or `new_context` action was sent.
Both hosts produced a V2 WebSocket compaction request with gateway-observed
`high` to `medium` rewriting, a completed native compaction containing a nonempty
server response ID, and subsequent ordinary requests continuing the task:

| Host | Native compaction completion (UTC) | Continuation |
| --- | --- | --- |
| CLI | 2026-09-08 19:17:28 | Resumed report review and completed the document task; client exit 0. |
| Desktop | 2026-09-08 19:24:07 | Continued task creation, authoring, and independent review. |

This confirms automatic summary routing in both real hosts, but **does not
qualify as a clean full orchestration parity run**. CLI inspection covered 73
host calls and 18 MCP events: one oversized report read was rejected and then
corrected, and an encrypted worker assignment remained policy-unverified.
Desktop inspection covered 103 calls and 18 MCP events: one invalid report read
was corrected, while two encrypted assignments remained policy-unverified. The
Desktop scenario was stopped after the successful compaction probe and review,
with an author correction and coordinator wait still active. Neither audit
failure was suppressed; complete current calls/events and audit results were
captured before the exact isolated clients were stopped.

An earlier exploratory run with a 55000 threshold also produced completed remote
summaries for both a coordinator and its native worker, but had a truncated file
read and a failed patch and was rejected as a clean run. The first Desktop
preparation attempt failed downloading a dependency; a longer download timeout
allowed the unchanged candidate to start. The stable installation and its active
tasks were not modified. Cross-model live routing was not exercised because
these tests use Luna; the live comparison demonstrates effort rewriting.

## 1.15.8 automatic provider and senior consultant — 2026-09-08

The candidate adds automatic Marketplace provider setup and a separate optional
senior consultant while preserving the seven tools and 22 executor profiles.
After the consultant follow-up repair, package/source-sync checks and all 382 tests
passed sequentially. Actual host
outcomes, rejected attempts, consultation usage and unrun scenarios are recorded
in the [focused verification report](../features/senior-consultant/verification.md).
The consultant's explicit Sol/Astra exception is separate from the ordinary worker
model policy below; it does not change the main session's model.

## Coordinator project-access enforcement — 2026-09-07

Recovery context now repeats the mandatory project-access boundary so compaction
cannot silently drop it. The hook accepts explicit coordinator identity when a host
repeats the bound parent session on a tool event and denies known command/file tools
before dispatch. Applying the same denial to an event without `agent_id` would also
block native workers sharing the parent session, so those events remain observable
and the audit rejects coordinator project access. Focused hook coverage exercises
the explicit denial and recovery reminder.

## Coordinator request-key runtime safety — 2026-09-07

Coordinator instructions now require literal UUID or stable literal `request_key`
values for Cortex calls that need a key and prohibit runtime generators such as
`crypto.randomUUID()` inside `functions.exec` wrappers. This closes the observed
failure where an unavailable host global raised before `create_task` reached the
server, leaving missing receipt and server-event audit records. Package validation
and the focused package regression assert that the orchestrator and pipeline
publication guidance retain this rule. Fresh CLI/Desktop qualification is separate
host evidence and remains required after the source stamp.

Worker publication guidance also requires the live `template` argument on the
initial `create_draft` call, using `general` for ordinary reports; empty argument
objects are invalid and are covered by the package regression.

## Coordinator project-access boundary — 2026-09-07

The rejected revision-8 CLI run recorded two `coordinator_forbidden_tool`
violations: coordinator `functions.exec` wrappers ran `sed` after task creation,
once before worker dispatch and once during post-report verification. The worker
completed its file mutation and report successfully, but the audit correctly
failed the run. The coordinator skill now requires dispatching a worker before any
project-target read, edit, hash or verification, including trivial one-file and
byte-for-byte requests. Package regression coverage asserts the guidance. The
coordinator may still read user-supplied sources and edit the exact Cortex-issued
pipeline draft; it must use worker receipts for project evidence.

## Worker app-message boundary — 2026-09-07

The previous protection was instruction-only: generated worker profiles prohibited
`codex_app.send_message_to_thread`, but the host still exposed that connector and
the observer did not classify the observed worker call. The live observer now marks
direct, quoted-bracket and simple static-alias `send_message_to_thread` calls,
including known `codex_app`/`functions.exec` wrappers, as
`forbidden_worker_app_thread_message`; orchestration audits fail when the route is
observed. This is bounded static inspection, not exhaustive JavaScript evaluation.
The bundled `PreToolUse` hook now also denies the canonical MCP operation and its
direct alias before dispatch for every active Cortex task. This is a precise
task-wide block because the documented tool event may not identify a worker; it does
not claim to revoke the connector outside an active task.
The installed Desktop provider is injected dynamically as the `codex_app` MCP
server. The candidate `[mcp_servers.codex_app] disabled_tools=["send_message_to_thread"]`
override fails bootstrap with `invalid transport`; the launcher does not claim a
per-tool filter or shadow the provider. The shared worker protocol treats the
boundary as hard and directs a worker to report an unavailable native channel in
its native result.

The coordinator's native-worker tracking route is explicit: subagents spawned via
`collaboration.spawn_agent` use only `collaboration.wait_agent`,
`collaboration.list_agents`, `collaboration.send_message` and
`collaboration.followup_task`. Codex app thread tools are reserved for explicit
user-owned task management and are not orchestration-worker controls.

Package policy coverage passed 27 tests and coordination observer coverage passed
16 tests after the change. The full suite passed 253 tests. The same isolated candidate was
exercised in both required hosts: the CLI and Desktop capability probes observed no
forbidden worker app-message route, and the native worker catalogues omitted both
exact app-message names. The ordinary CLI and Desktop acceptance audits completed
native reports but failed on coordinator policy violations; those nonzero outcomes
remain part of the evidence and do not constitute clean acceptance. Full details
are retained in the worker app-message verification checkpoint report.
Internal native lifecycle/status observations remain permitted after a wait because
they do not write a worker message into the app thread; the audit's forbidden route
classification applies to thread-visible app messaging. The CLI helper now retains
the exact native archive provenance marker needed to accept its separator newline
without accepting changed request text.

## Current worker-model policy

The coordinator route is now explicit: Luna is the default/priority model for
ordinary work and all research, exploration and analysis assignments; Terra is
reserved for complex work; Sol is limited to narrow
security-analysis microtasks and never security implementation. Worker effort is
medium/high/xhigh for Terra and Sol. Reviews record the inspected implementation
model/effort when relevant and do not automatically escalate. Other
models or efforts remain disallowed unless directly requested by the user, whose
override is preserved. The isolated observer's `worker_model_policy` checks these
routes from observed assignment/participant metadata.

## 1.15.7 fresh draft delivery identities

Candidate `1.15.7+codex.sha256.b5503c36f31d590b` makes draft creation keys optional.
Unkeyed creation generates a fresh UUID; explicit retries still reject changed
arguments and preserve accepted evidence. The seven operations, storage format
and 22 profiles are retained. The creation annotation no longer claims unconditional
idempotence, and its schema explains recovery through the own-draft catalogue.

All 21 focused host-context/schema tests passed, followed sequentially by package
validation, source-only sync and all **243 tests in 11.11 seconds**. Regressions
cover a reused worker after publication and restart, changed report templates,
concurrent worker isolation, unfinished-draft discovery and explicit-key conflicts.

One real CLI session completed an ordinary documentation request and a follow-up
using the same native worker. All six creations omitted explicit keys and received
distinct UUIDs; two worker reports and four newest-first pipeline editions were
published without MCP errors or replays. Original evidence, stored hashes, the
product result, two source messages and absence of unfinished drafts were checked.
The session exited zero and was stopped after complete calls/events capture and
audit. The audit exited one: a coordinator project-discovery flag and a recovered
worker command failure plus two recovered draft-patch failures remain recorded.
This establishes focused delivery behavior, not clean protocol qualification.
Desktop, forced compaction and CLI/Desktop parity were not run on this candidate.
See [release readiness](../release-readiness.md).

## 1.15.7 graph discovery and native worker updates

The shared worker protocol explicitly discovers `codebase_memory` independently
of Cortex report tools and uses graph evidence for structural code questions before
filesystem symbol searches. The declared reference covers exact workspace matching,
duplicate indexes, coverage, pagination, initial indexing and source fallback.
Progress, questions, blockers and verification updates stay on the native parent
channel, including before the automatic final handoff; app task messaging is forbidden.

The `1.15.7+codex.sha256.03279e8f757a5b4d` candidate passed package validation,
source-only sync, 56 focused tests and all 241 tests sequentially. Profile generation
and existing size bounds passed. Real qualification investigates an ordinary discount
bug with API, invoice and CLI callers; review worker graph calls, exact index selection,
source evidence, concrete reproductions and every native/app message route. The CLI's
`--codebase-memory` flag is necessary. Keep missing-index fallback evidence separate
from indexed graph qualification. See [release readiness](../release-readiness.md)
for actual host outcomes and retained unsuccessful attempts.

The final CLI and Desktop workers loaded the full skill before tool discovery,
used the correct index for eight and nine graph calls respectively, and returned
one native result each without app task messages. Both workspaces passed independent
reproductions, existing tests and protected-file/store checks. Desktop's audit passed;
CLI retained coordinator project-read/status-probe flags. Desktop omitted the coverage
API call, and neither scenario exercised a separate immediate native message.
Strict full-protocol CLI/Desktop parity remains unverified; focused graph routing and
the absence of app messaging are the observed results.

## Worker language from the first response

Candidate `1.15.6+codex.sha256.879d600bfb9e3966` requires English-only worker
reasoning and communication from the first response, including commentary before
skill loading and after context recovery. Assignments carry that requirement;
the coordinator's user-facing language rule is explicitly scoped to its own role.
The shared source generates all 22 worker skills and TOML profiles.

Stamp, package validation, source-only sync, 27 package tests and the complete
**241-test suite (10.82 seconds)** passed sequentially. All 24 changed skills
passed skill validation, and generated profiles match their source.

One real CLI and one real Desktop scenario used the same unchanged payload and a
Russian request for English documentation. All nine worker messages were English,
including both initial messages before any tool call; all seven coordinator
messages were Russian. Each native result was delivered once. Both examples,
protected-file hashes and archive integrity checks passed. Desktop's audit passed;
CLI retained a report-reference audit flag, so strict consecutive qualification
remains unverified. See [release readiness](../release-readiness.md) for the
flag's evidence and the limits of these focused checks. Forced compaction and
unexposed internal reasoning were not verified by the message-language checks.

## Response language and native handoff source evidence

Candidate `1.15.6+codex.sha256.cc594f16f07f34cf` preserves version 1.15.6, the seven
MCP operations and 22 profiles. The coordinator's language guidance now covers
updates, questions, final answers and recovery, while distinguishing the user's
own prose from English reports and forwarded agent messages. The shared worker
protocol explicitly keeps result delivery in the native final response.

Stamp, package validation and source-only sync passed sequentially. All 27 package
tests passed; the complete suite passed **241 tests in 10.79 seconds**. All 25
changed skills passed skill validation, and the 22 generated worker skills and
TOML profiles match their shared source. Live language checks use a Russian product
request for an English README, with ordinary worker reporting and native delivery.
This checks observable response language rather than inferring behavior from a
prompt-text assertion. See [release readiness](../release-readiness.md) for host
outcomes and retained failed attempts. All four language/handoff scenarios passed,
with one native worker result and no cross-task message per run. Individual CLI and
Desktop audits passed, but a consecutive successful pair remains unverified because
the other runs retained unrelated status-probe and patch-construction failures.
Forced compaction, explicit response-language switching and an exhaustive set of
languages were not exercised by these focused scenarios.

## Project-local storage source evidence

Candidate `1.15.6+codex.sha256.4b26dcd06eb65e14` passed stamp, package validation,
source-only sync and 128 focused tests sequentially. The complete suite passed
**241 tests in 10.76 seconds**. The new coverage verifies cross-process lock
isolation between projects, same-project concurrent writes and foreign keys,
report/draft boundaries, native parent routing, restart and missing-index behavior,
project-only retention, rejected-file preservation and offline archive splitting.
The isolated observer additionally accepts an exact single-quoted Python `-c`
skill read after validating its syntax tree; shell expansion and extra operations
remain rejected. All 29 observer tests pass. Fresh ordinary CLI and actual Desktop
qualification passed consecutively on the same unchanged payload. CLI reviewed
66 host calls, 11 MCP events and 31 hook actions; Desktop reviewed 59 calls, 14 MCP
events and 27 hook actions. Both Cortex audits exited zero. Independent product,
archive and retained-store checks passed. The initial CLI attempt and its resume
remain disclosed as protocol rejections, despite successful routing/recovery checks.
See [release readiness](../release-readiness.md) for exact scope and measurements.

## Historical v9 source evidence

Candidate `1.15.6+codex.sha256.0066c0266f853fd5` preserves version 1.15.6, the
unchanged seven-operation catalogue and 22 specialist profiles. Stamp, package
validation and source-only sync passed sequentially; the focused suite passed 69
tests and the complete suite passed **208 tests in 8.90 seconds**. All 30 skills,
profile generation and `git diff --check` passed.

The hook benchmark used 100 fresh subprocesses per path. P95 including startup was
39.017 ms inactive, 47.452 ms for active tool receipts and 50.982 ms for deferred
prompt capture. Whole-task overhead at or below 5% remains unverified. V5 was
rejected for incomplete initial command receipts and truncated catalogue discovery;
its product checks passed, while recovery later stalled after draft creation and was
cancelled before lifecycle state was known. V6 completed normally with product checks
7/7 and no MCP or hook failures, but protocol qualification failed on the initial
coordinator receipt and a recovered worker's initially failed draft-report hash
correction patch, which was later repaired. V7's CLI product checks passed 8/8 with
complete receipts and no truncation, but post-publication handoff lookup was rejected.
Its exploratory Desktop run passed product checks 8/8 in 170.432 seconds using
1,686,903 tokens. The trace contains 74 host calls, 13 successful MCP events and 30
successful hook receipts; two catalogue searches were truncated across four wrapper/nested records and a
status probe was rejected. V8 was source-only and never live. V9 adds the concrete
names-only discovery example. Its final consecutive CLI/Desktop pair passed on one
unchanged payload with Luna/high coordinators and Luna/medium workers. All 86 CLI
calls / 12 MCP events / 37 hook actions and 70 Desktop calls / 14 MCP events / 30
hook actions were reviewed. Both audits exited zero; product examples, independent
contract checks and protected-file hashes passed. The CLI retained two recovered
project patch failures; Desktop had no tool errors. CLI exited normally, and both
isolated sessions were stopped after full evidence capture. See
[release readiness](../release-readiness.md) for exact identity, usage, scope and
retained unsuccessful attempts. Three observer false flags were corrected in MAIN:
two for safe literal quoted-Python full-skill reads and one for exact Desktop
escaped-underscore plus terminal-newline source fidelity. Storage retains exact native
bytes and the observer guard verifies delivery before accepting an exact archive hash.
Quoted-heredoc AST validation rejects shell expansion, with regression coverage added.
The separate frozen v4 pilot remains 12/12 and is historical comparison data.

Source checks require Python 3.11+ with `pytest` and `jsonschema` installed.
They run sequentially on a current content-stamped payload:

```bash
python3 -B scripts/cortex_package.py stamp
python3 -B scripts/validate-cortex-marketplace.py
./scripts/sync-cortex.sh --check
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q
git diff --check
```

The focused suite covers seven advertised operations, original request recovery,
advisory governance, real Markdown files, a single newest-first pipeline,
immutability, duplicate delivery, changed-delivery conflicts, task isolation,
concurrency, file publication recovery, Unicode cursor reads, stable newest-first
catalogue snapshots, process restart, safe actionable errors, input/output schema conformance,
whole-file publication beyond 16 MB, file-backed pipeline publication beyond the MCP
request limit, typed server-created drafts, per-thread draft ownership, source cleanup,
disk failure, short reference collision recovery,
complete native profile payloads, host-metadata task binding, nested parent
inheritance, automatic pipeline selection and package identity.

The project storage tests hold project A’s SQLite writer lock in another process
and verify project B completes before that lock is released. Same-project writes
remain serialized; concurrent tasks retain their bindings and foreign-key consistency.
These source checks do not replace real CLI/Desktop qualification.

For real execution, use a separate existing test project and start
`./scripts/cortex-live-smoke start --workdir PATH`. The helper creates only the
exact `cortex-markdown-smoke` session on the default tmux server and attaches an
owner-private output-only observer before entering `scripts/cortex-dev`.
That entry point refreshes and installs only `$HOME/.cortex-dev/.codex`.

For Codebase Memory routing qualification, add `--codebase-memory` to `start`:
the CLI helper deliberately disables that server by default. Verify actual worker
graph calls before filesystem symbol discovery, exact-workspace index selection,
source snippets, caller tracing and coverage checks on a structural product task.
A README-only scenario or a run with the server disabled cannot qualify this route.
On Desktop verify that the isolated candidate configuration enables the server.

Inspect `capture` and `status`. A visible trust screen permits one explicit
`enter`; then visibly confirm the composer. Inspect `events` for the passive
server initialization receipt and compare package path, version and catalogue
digest with the candidate. A receipt is observation, never task authority.

Send a prompt file beginning with the actual `$cortex:orchestrator` skill token
and an ordinary product request appropriate to the changed behavior, without
teaching the model orchestration steps in the prompt. `send --prompt-file FILE` inserts the
complete literal text, waits five seconds and sends one named Enter. It does
not infer acceptance. Observe task creation, pipeline writing, native delegation,
selective report reading and worker publication. Send a concrete product change;
verify the same task and pipeline identity, newest edition first, added work and
final result. Inspect metadata-only `events` for all worker calls and errors.
Use `calls` for the complete coordinator/worker host history and run `audit`
before accepting or stopping the CLI session. The CLI commands share the Desktop
observer, policy checks and command-session accounting.
Before adopting a new host-context mechanism, observe the actual incoming
coordinator and child metadata on both CLI and Desktop first. Verify exact parent
linkage; never infer support from upstream code alone.

After exiting ordinary Codex, confirm that the owned pane is an idle live bash,
that the current capture contains exactly one `Cortex live-dev exit=0`, and that
the ownership-bound process snapshot has no descendants. Phase 2 collection
repeats the status and process snapshot before sealing evidence, and `stop`
rechecks them against the bundle before stopping the exact session. CLI smoke
runs derive the store from the canonical workdir at
`PATH/.codex/cortex/cortex.sqlite3`. Resume with the same workdir and
`--resume-last`; it reuses that exact project-local store and rejects a missing or
mismatched store. `stop` preserves the store for resume and removes only the
session and observation streams. Confirm the existing task, selective report
recovery and no replacement task creation.
For a new evaluation start, pass `--evaluation-fresh-store`. This explicit Linux-only
CLI guard requires a supported architecture/syscall mapping and same-filesystem
staging, builds the complete owner-private `.codex/cortex` hierarchy in an external
same-filesystem stage, and installs it once as the project `.codex` destination with
no-replace semantics. Unsupported, unavailable, raced, or otherwise unproven
conditions fail closed before canonical project mutation; uncertain loser stages
are retained outside the project rather than removed by pathname. Git/config
readiness is completed before that commit. It
rejects an existing/symlinked/non-private/escaping database target and leaves
`cortex.sqlite3` absent for runtime initialization. Its launcher receipt contains
only opaque, project-relative and digest-based provenance. It performs no delete,
truncate, migration, overwrite, or acceptance of existing state; ordinary and
`--resume-last` starts are unchanged.
Both live helpers require the supplied workdir to already be a Git repository
root. They never run `git init` or write local identity settings. Create a fresh
empty directory with `mktemp -d` and run `git init` explicitly for an isolated
qualification. A worker Git probe that would fail without `.git` remains advisory
observer noise rather than a coordinator-boundary violation.
Finish by collecting the sealed evidence from the stable task-terminal composer
or exited bash. The composer path requires repeated stable process, capture, calls,
events, and audit state, explicitly completed waits, and an exact final report/
pipeline call-event identity join; then use bundle-gated
`stop --interrupt`. The bash path retains its exit-marker requirement. Orphan
recovery remains dead-pane-only and repeats plus hashes status, process, and marker
capture immediately before stop. Stop only the exact session.
The report and draft identities must each be nonempty, correctly typed and match
`r_[0-9a-f]{12}` / `d_[0-9a-f]{12}` on both the final call and event before the
join; missing, empty, malformed, wrong-type, mismatched, or equal-null values reject.
Never kill the tmux server or use `codex exec` as native evidence.

Actual Desktop uses
`scripts/cortex-desktop-dev start --workdir PATH --prompt-file TASK_PROMPT.txt`
with `CORTEX_DESKTOP_BINARY` pointing to the real executable. The helper prepares
the same candidate, uses a disposable Electron profile, and opens exactly one new
Desktop task with the literal prompt from the regular UTF-8 file. The project-local
store keeps a run tied to its canonical workdir; the helper uses a disposable
Desktop profile separately. Omit `--prompt-file` only for manual UI entry. Source
checks are not Desktop evidence;
disclose missing credentials, display access or unrun host checks. See the
recorded [release evidence](../release-readiness.md).

After the dev window is visible and the prepared composer is confirmed, run
`scripts/cortex-desktop-dev send`. It resolves exactly one visible ChatGPT window
whose X11 PID equals the isolated process recorded by `start`, rechecks ownership,
submits once, and refuses duplicate submission. It never targets another
Desktop instance by title alone.

The Desktop `send` command focuses the prepared composer, uses `Ctrl+Enter`, and
acknowledges delivery only after exactly one new task appears in isolated host state.
Use `scripts/cortex-desktop-dev events` while the task runs and
`scripts/cortex-desktop-dev audit` before accepting it. The audit consumes the
complete private metadata journal from every coordinator and worker MCP process
and fails if any non-initialization operation has an outcome other than `success`.
Read events identify pipeline versus ordinary report, the short selected report ID,
and start versus continuation without logging cursors, Markdown, request text or
other arguments. This makes selective worker reads directly auditable.

Use `scripts/cortex-desktop-dev calls` to inspect every host-tool invocation from
the coordinator and native workers. With no `--limit`, it emits the full run:
wrappers, each statically visible nested host call, and each actual Cortex MCP event.
It also records every host `CommandExecution` item separately, so one wrapper that
starts several commands cannot hide an individual nonzero exit or truncated result.
It retains only timestamp, thread lineage, observed role/model/effort, safe spawn
selectors, tool name, argument/result digests, bounded routing selectors and safe
error codes. Review
every call for a concrete need and correct role ownership. The final `audit` rejects
unsuccessful MCP events, Cortex call errors or truncation, missing Cortex outcomes,
forbidden coordinator, plugin, or draft access, worker calls after publication without a successful explicit parent follow-up
after the final handoff, handoffs missing the latest same-worker publication or
including unknown/other-worker report references, oversized document pages and command sessions without a terminal
result. Shared or coupled mutation resources retain one owner; profile names alone do not
prove overlapping ownership. Every tool error remains in the
chronological `tool_error_history`; its Cortex subset fails the run, so a corrected
`create_draft` parameter failure cannot disappear behind its successful retry. Patch wrappers must preserve the exact native patch input and expose complete
receipts; safe string syntax is not itself a protocol error. Catalogue and pipeline reads should answer a concrete missing fact; selected
evidence rereads are not rejected merely for repetition. An exact successful command retry records
the earlier host-check failure under `resolved_host_failures`; it is never erased.
If a run must be rejected, collect the complete current `calls` and `events`
and execute `audit` before stopping that isolated run. One early fault must not hide
other calls that were already dispatched.
The Desktop helper scopes `calls`, `events`, `hooks`, usage, and open-resource
accounting to `session.json`'s submitted coordinator and its native descendants.
Other recent same-workdir roots are listed as `foreign_task_roots` and fail the
audit; their worker sessions and lifecycle records are not attributed to the
selected task. Conflicting row identities fail closed. Duplicate edges,
self-parenting, cycles, and malformed recent edge components are emitted under
`invalid_task_topology`; cyclic foreign components are also retained under
`foreign_task_roots`, so they cannot disappear behind an empty roots array.
The `usage` action consumes this validated inventory and fails explicitly with
`invalid_topology` rather than recomputing an overwrite-prone child-parent map.
Only the exact advertised orchestrator `SKILL.md` is a valid coordinator cache
read; worker skills, other skills, and references remain forbidden.
For the next c4 follow-up parity qualification, create a fresh temporary empty Git
project with `mktemp -d` and `git init`, then use that exact directory for both
unchanged-candidate CLI and Desktop runs. This is experiment isolation for that
qualification, not a mandatory Cortex product stage.

Browser-dependent workers run sequentially when the host exposes one shared browser
surface. Each worker uses only tabs and command sessions created in its own native
thread; the live audit rejects process hunting and unresolved cross-thread resource
failures instead of accepting a later summary as proof.


Both ordinary CLI and actual Desktop scenarios are required on the same unchanged
payload. When the product work needs clarification, answer the genuine question
as ordinary chat text and verify that the answer is incorporated. The PR workflow runs only for pull requests targeting
dev or main; automated source tests do not substitute for either real host.

Live qualification assesses Cortex coordination, not the LLM's development ability.
Project command and browser failures remain visible in separate diagnostic fields;
they do not independently fail Cortex acceptance. Draft-edit failures, invalid MCP
calls, report ownership violations, duplicate publications, and unresolved sessions
at report publication do fail it. Do not describe project diagnostics as Cortex faults.

Resume observation retains the original thread-creation boundary while selecting
only rollout calls made after the resumed session started. Prompt receipts include
the existing coordinator thread. Old calls are neither lost nor mistaken for new
MCP deliveries; the previous run's audit remains separate.

Native-profile installation regression checks must begin with an empty agents
registry. Verify that the package alone reports 22 missing profiles, explicit setup
registers exact bytes, conflicting user files prevent all writes, and subsequent
managed updates preserve unrelated profiles. This optional personal export check is separate from marketplace preparation;
live preparation must not install these exports.

Marketplace parity requires a personal agents directory with no Cortex TOMLs;
`prepare_codex.py` must only install the plugin through the marketplace. Verify
that each native worker loads its complete packaged skill by native injection or
its exact advertised SKILL.md path, then publishes its own report. An MCP-only pass
or a dev-only native registry does not prove ordinary installation.

Targeted skill/declared Markdown reference reads are valid instruction loading;
TOML, server internals and plugin enumeration remain forbidden accesses. A mixed
command is not classified as instruction-only, but an approved skill read alongside
ordinary worker project discovery is not itself forbidden cache access. Review each
access and the participant's role, full loading evidence and truncation.
The observer preserves this distinction through simple `rg`/`grep` pipelines:
quoted private-cache exclusion patterns are static mentions, while direct private
operands, unquoted globs and opaque shell syntax remain unauthorized. A valid
declared Markdown reference is instruction loading, not a duplicate complete
worker-skill receipt.
An exact Python `-c` or quoted-heredoc audit helper may compare a literal private
marker as data. The exception requires every marker literal to be a direct Python
comparison operand; assignment, file/process calls, comments, interpolation,
malformed code and a private working directory remain unauthorized.
Worker final handoffs name only the report published for that assignment. Input,
pipeline, catalogue, predecessor and other-worker report IDs belong in the saved
report body so cited evidence cannot be confused with worker-owned publication.
Encrypted assignment content remains opaque. See [host compatibility](host-compatibility.md).
A standalone wrapper text item `exit_status=N`
is an explicit command receipt; stdout containing that string is not sufficient.

CLI submission uses one named tmux buffer and bracketed paste, preserving internal
newlines, indentation and repeated spaces. Receipt matching no longer collapses
whitespace. Only trailing line terminators from the prompt file are removed.

The isolated live observer compares a private SHA-256 of the submitted task text
with the request retained by task creation. It ignores only the leading route
token and outer whitespace; added host envelopes, translations and internal
formatting changes fail qualification. Diagnostics expose only the comparison,
not the request. CLI submission preserves multiline text with a single bracketed
paste, and resume preserves the original comparison reference. Worker instructions
require complete loaded skills and structured command receipts. Exact advertised
skill and needed declared Markdown reference reads are valid; installation exploration,
TOML and server-internal reads are forbidden. Printed shell markers alone are not
success evidence. A successful backend execution does not excuse a wrapper
that exposes only stdout: the observer retains `wrapper_outcome=unverified` and
reports `command_wrapper_missing_receipt`, including for skill reads.

Hook coverage is a separate boundary. On the observed CLI host, the Bash
PostToolUse response is exactly stdout, without the native execution envelope.
Consequently, even JSON-shaped stdout or printed wrapper headings remain unverified
in the hook. Native execution receipts can still establish the command outcome,
while the model-facing wrapper must preserve that outcome for protocol acceptance.

The coordinator maintains a useful durable pipeline for decisions and recovery.
Bounded discovery may precede the first meaningful edition; the observer does not
turn pipeline publication into a universal pre-delegation stage.

Desktop rich-text formatting is accepted only when independently present in the
recorded user message and traceable to the prepared prompt: a blank line before
an ordered list and literal underscore escaping. Other spacing and source changes
remain failures. This exception does not apply to CLI or unobserved model changes.

Native-source tests exercise the real read-only host index and typed rollout
records: exact current-turn selection, thread/project isolation, bounded-memory current-turn search,
symlink rejection, absent-source failure, literal credential redaction and replay
without a remaining host source. Live creation receipts now expose the server's
stored-source digest; the observer compares that receipt against independently
recorded input rather than a model-authored request argument.

Steering tests cover multiple queued corrections, exact whitespace and Unicode,
restart, repeated native receipts, accepted-replay deferral followed by fresh capture, transactional
rollback, source rewrite, partial host records, foreign-thread exclusion and
literal credential redaction. Real-host qualification must additionally compare
archived source against native UserMessage receipts across steering and resume.

The passive call audit records typed native coordinator user-input events without
message bodies. A new user event permits delivery of steering after a native wait
and invalidates a previous unchanged-catalogue observation. An unrelated injected
user-role envelope does not provide that permission.

Coordinator checks permit necessary original-source and evidence continuation pages;
4,000 characters remains a per-page bound. Substantial project execution stays delegated. Follow-up
checks resolve the exact target in the observed native lineage, require prior report
publication and final handoff, and retain command/session accounting for each assignment.
Failed delivery, a plain message, an unrelated target or a report from another worker
never reopens ownership. Role suitability and independent review remain model decisions.
On resume, prior successful publication receipts and matching final handoffs reduce
to metadata-only assignment snapshots. Old calls are not replayed as current-run
evidence. An intervening worker action invalidates the completed snapshot until a
new publication and handoff are observed. Intact worker context may retain its schemas;
actual context loss still requires the documented recovery procedure.

Use the separate [outcome suite](quality-evaluation.md) to measure quality and cost;
protocol qualification alone does not demonstrate improved task-solving ability.

The bounded ASCII fixture steering route has an exact native receipt check. A prior
run changed a typed Latin character to a Cyrillic lookalike and was rejected; after
allowing composer focus to settle and slowing synthetic typing, the same fixture
text arrived exactly in the real Desktop task. This is evidence for this host route,
not a guarantee for arbitrary keyboard layouts. Inspect the actual message before
any further input after failure; never replay an uncertain submission. Visually confirm
the empty composer and use `scripts/cortex-desktop-dev steer --prompt-file FILE
--composer-x X --composer-y Y` with coordinates observed inside that exact window.
The helper checks the isolated PID, types once and submits with Ctrl+Enter, then
requires one exact typed UserMessage receipt on the retained task, allowing only the
observed single editor-added paragraph newline. It does not infer
readiness or semantic acceptance, change the clipboard, or replay uncertain input.
Non-ASCII follow-up transport is unverified; the initial prepared-prompt route keeps
its existing Unicode behavior. Initial submission still uses `send` and verifies a
new task receipt.

Audit regressions also cover an active worker message followed by one coordinator
reply after a wait. A reply to a different worker, a duplicate reply or a failed
inbound message must not receive that exception. Message bodies remain private.

Patch-access regressions distinguish evidence text mentioning a skill path from
actual add/update/delete/move targets inside the installed plugin. The observation
layer records accesses and outcomes; report prose alone is not filesystem access.

Concurrent report-read correlation uses retained literal report identities where
available, including a single local const binding. Ambiguous intervals or bindings
remain separate evidence rather than being matched by call order.

Generated skill checks compare complete bytes and final completion markers. The
profile body and generated reference sets must match their shared sources and
optional TOML exports; a partial read is not complete instruction loading.

A successful native MCP receipt must not inherit a later JavaScript consumer error
as an MCP-error policy flag. The consumer failure remains in the complete audit;
a genuine MCP error or absent receipt is not exempted.

## Format 11 and lifecycle-hook verification

Focused regressions exercise a corrupted neighboring pipeline, unavailable and long
native journals, distinct identical messages, unavailable attachments, changed-file
cache invalidation, binding/state/provenance receipts, incomplete drafts, offline
migration, patch-content false positives, unknown subagent identity and hook failure.
Existing fault tests cover competing publications and process exit before commit.
Report correctness, recovery and protocol independently; retain failed attempts.

The current stamped source suite passed **241 tests in 10.76 seconds** against
payload `1.15.6+codex.sha256.4b26dcd06eb65e14`. The mandatory scenarios
map to these unit regressions:

| Scenario | Source-backed unit coverage |
|---|---|
| Independent projects and same-project concurrency | [`test_project_database_locks_are_independent_across_processes`](../../tests/test_project_storage.py) proves independent writes while another project is locked; `test_multiple_tasks_in_one_project_remain_consistent` verifies same-project task bindings and foreign keys. |
| Native project routing and recovery | [`test_child_requires_indexed_parent_and_rejects_cross_project_or_edge_conflicts`](../../tests/test_project_storage.py), `test_restart_routes_from_index_without_rollout_and_preserves_archive` and `test_missing_or_corrupt_native_index_has_no_global_or_cwd_fallback` cover routing limits. |
| Safe rejection and retention | [`test_rejected_database_keeps_bytes_and_journal_mode`](../../tests/test_project_storage.py) preserves incompatible files; [`test_retention_command_uses_only_selected_project_store`](../../tests/test_retention.py) leaves a legacy global store unchanged. |
| Explicit archive split | [`test_split_preserves_all_selected_metadata_and_never_copies_markdown`](../../tests/test_project_split.py) checks selected rows and backups; `test_split_holds_source_sqlite_and_destination_access_locks_through_publication` verifies full lock lifetime. |
| Neighbor corruption | [`test_corrupt_neighbor_never_blocks_restart_or_other_task`](../../tests/test_storage_v11.py) isolates recovery to the selected task; [`test_hook_load_does_not_recover_corrupt_neighbor`](../../tests/test_hooks.py) keeps hook loading from touching the damaged neighbor. |
| Unavailable or long host journal | [`test_archive_reads_survive_capture_failure`](../../tests/test_storage_v11.py) and [`test_unavailable_new_source_does_not_hide_saved_reports`](../../tests/test_host_source.py) preserve archive access; [`test_current_turn_boundary_can_precede_more_than_eight_megabytes`](../../tests/test_host_source.py) exercises a single oversized irrelevant host record without a tail-window fallback. |
| Concurrent publication and crash before commit | [`test_concurrent_delivery_once_and_distinct_writes`](../../tests/test_markdown_store.py) covers competing deliveries; [`test_crash_before_pipeline_commit_restores_backup`](../../tests/test_source_reports.py) covers process exit and task-scoped recovery. |
| Steering, clarification and partial cancellation | [`test_every_steering_is_exact_ordered_and_survives_restart`](../../tests/test_host_source.py) preserves ordered native messages; [`test_compaction_hint_dedup_survives_restart_and_new_source_reopens_hint`](../../tests/test_hooks.py) uses a newly archived partial cancellation to reopen recovery context. |
| Compaction and restart | [`test_compaction_hint_dedup_survives_restart_and_new_source_reopens_hint`](../../tests/test_hooks.py) verifies bounded, deduplicated compact recovery across a store restart and a later source revision. |
| Missing attachment | [`test_attachments_retain_recovery_locations_and_explicit_gaps`](../../tests/test_host_source.py) records available file/resource recovery and an unavailable gap; [`test_attachment_only_original_is_archived_as_an_explicit_gap`](../../tests/test_host_source.py) covers an attachment-only request. |
| Unfinished worker | [`test_bound_child_stop_reports_own_open_draft_without_continuing`](../../tests/test_hooks.py) reports only the worker's unfinished draft and does not force continuation; [`test_reused_published_worker_stop_marks_unknown_assignment_boundary`](../../tests/test_hooks.py) retains the unobserved-boundary diagnostic. |
| Hook failure | [`test_record_failure_returns_nonblocking_error_and_private_failure_receipt`](../../tests/test_hooks.py) preserves a private failure receipt and pending source without fabricating success. |
| Wrong participant binding | [`test_subagent_lifecycle_requires_explicit_mapping_and_rejects_conflicts`](../../tests/test_hooks.py) and [`test_unknown_parent_or_wrong_project_cannot_bind_worker`](../../tests/test_hooks.py) reject conflicting, unknown-parent and wrong-project bindings. |
| Patch text false positive | [`test_patch_mentions_in_content_do_not_block_but_registered_mutation_does`](../../tests/test_hooks.py) distinguishes added prose from an actual protected-path mutation; [`test_report_patch_mentions_are_not_plugin_access_but_patch_targets_are`](../../tests/test_decision_coordination.py) applies the same distinction in the live-call audit. |
| Normal/resume pending-source retirement | [`test_normal_interval_is_not_archived_and_current_reactivation_is_preserved`](../../tests/test_storage_v11.py), [`test_unavailable_resume_boundary_remains_pending_until_authoritative_source`](../../tests/test_storage_v11.py) and [`test_normal_transition_retires_only_accepted_skipped_pending_signals`](../../tests/test_storage_v11.py) cover pause, authoritative resume and task-scoped retirement. |
| Exact replay with redaction | [`test_accepted_write_replay_does_not_apply_old_redactions_to_new_source`](../../tests/test_storage_v11.py) proves replay skips unrelated pending source and its historical redactions; [`test_steering_redaction_and_delivery_replay`](../../tests/test_host_source.py) proves the next fresh call captures that source exactly once. |

This table is unit-test evidence for storage, source and hook behavior. The
separate [release evidence](../release-readiness.md) records the consecutive real
CLI/Desktop pair, retained earlier rejections and source-only hook timing. Unit
results alone do not establish host coverage, transport fidelity or performance.

Hook streams are distinct from model/MCP calls. A parent session ID alone does not
prove the actor of a tool hook. Verify explicit lifecycle agent IDs against the task
binding and later MCP receipts. `UserPromptSubmit` without a unique native message
receipt records deferred capture, not a fabricated duplicate-free source. Verify
later capture using typed user receipts and preserve optional credential redactions.

`SessionStart(resume|compact)` supplies at most roughly 1,000 tokens of safe recovery
references and counters, with unchanged hints suppressed. Pre/PostCompact stdout
is not the recovery channel. Stop diagnostics do not force continuation and cannot
prove a reused worker's assignment boundary when the host does not expose one.

Use normal host hook trust and confirm actual event support on the installed CLI
and Desktop. Source tests are not proof of real hook coverage. Measure fresh Python
handler subprocess p95 against 100 ms and whole-task overhead against 5%; report
unmeasured limits. The three-configuration four-scenario pilot is documented in
[quality evaluation](quality-evaluation.md). CLI/Desktop parity still requires
consecutive successful runs on one unchanged full payload.

Live development uses Luna/high for the coordinator and Luna at medium/high for
native workers. The isolated helpers layer this user-requested test policy and audit
actual participant/selector receipts. Heavy live-test models are rejected. This does
not change stable settings or the plugin's general user-selected model policy.

The final 1.15.8 consultant repair passed 382 tests and consecutive focused CLI
then actual Desktop audits on unchanged payload `3443aff2cf72630c`: 50/62 host
call records and 13/14 MCP outcomes, respectively. Both audits exited zero.
See the [detailed report](../features/senior-consultant/verification.md) for token
usage, earlier rejections, observable-route limits and unrun scenarios.
