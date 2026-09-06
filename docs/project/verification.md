# Verification

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

After exiting ordinary Codex, capture `Cortex live-dev exit=0`, then `stop` the
exact session. CLI smoke runs derive the store from the canonical workdir at
`PATH/.codex/cortex/cortex.sqlite3`. Resume with the same workdir and
`--resume-last`; it reuses that exact project-local store and rejects a missing or
mismatched store. `stop` preserves the store for resume and removes only the
session and observation streams. Confirm the existing task, selective report
recovery and no replacement task creation.
Finish by stopping the exact session; after failure use `stop --interrupt`.
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
