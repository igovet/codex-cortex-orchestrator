# Current Cortex reliability verification — 2026-09-05

The current candidate retains semantic version **1.15.6**:
`1.15.6+codex.sha256.e3cbb23ec9b5e373`, payload SHA-256
`e3cbb23ec9b5e3732d65dea1435269cccbf6e438b9fcd725ae194657694e5afc`.
It exposes seven tools and 22 generated native specialist profiles.
Catalogue SHA-256:
`878180556042e55d775eae07ff0023773cd7d418fc20ae9dc07bda9c3e8229c3`.

Package validation and source-only sync check passed. The complete source suite
passed **83 tests**. New regression evidence covers process death after pipeline
rename but before SQLite commit, directory-sync failure after rename, preservation
of the prior pipeline edition, exact retry without duplicate editions, unfinished
markers in all eight draft templates, and uncertain CLI submission without automatic
reinsertion. Resume observation tests cover existing native threads and exclusion
of old call history. The live observer also recognizes relative draft patch paths.

Coordinator and generated worker instructions now agree on one in-place patch
with independent marker replacements. Concrete evidence dependencies determine
additional discovery/planning, replacing the arbitrary acceptance-category threshold.

Live qualification measures Cortex: task inheritance, tool sequencing, draft
ownership, publication, selective reads, pipeline identity, and native handoff.
Project-development failures are separate diagnostics, not Cortex acceptance failures.
The preliminary CLI run on `bad409fb439ce553` passed its Cortex audit and exited zero;
it exercised four native specialists and six first-attempt publications.
The final CLI run passed its Cortex audit: 13 non-initialization MCP operations,
67 observed host-call records, four first-attempt publications, two native workers,
zero Cortex errors, replays, or report/coordination policy violations. CLI exited zero.
A real resume on the same workdir and store then passed: seven task operations,
41 host-call records, two first-attempt publications, the same coordinator and pipeline,
no new task creation, and exit zero. The resumed user prompt was delivered once.
Actual Desktop then completed on that unchanged payload and passed its Cortex audit:
14 non-initialization MCP operations, 72 host-call records, two native workers,
five first-attempt publications, and zero Cortex errors, replays, policy violations,
or open command/exec sessions. The prepared composer was visually confirmed before
one submission; the host recorded one new task receipt and its completed final answer.
The disposable Desktop process was stopped through its helper. CLI and Desktop
therefore have consecutive successful real-host Cortex checks on this payload.

A read-only storage audit after both hosts stopped verified all **11 current report
files** across the final CLI/resume and Desktop stores: committed size and SHA-256,
owner-private permissions, SQLite integrity and foreign keys, one pipeline per task,
no pending source deletions and no unpublished drafts. CLI/resume retained one task
and three pipeline editions; Desktop retained one task and three pipeline editions.
Stable user plugin/configuration were not installed or updated.

These focused runs establish the exercised Cortex lifecycle, not universal future
LLM correctness. All 22 profiles, harvest/refresh, and native-worker compaction were
not individually exercised in this qualification. Crash and large-document behavior
are source/stdio evidence rather than deliberately destructive live-host scenarios.

The sections below are historical observations on other payloads. Their counts,
formats and statements about a then-current candidate do not describe this candidate.

# Historical release observations

## Candidate and source checks

The replacement retains semantic version **1.15.6** as requested. Current payload:
`1.15.6+codex.sha256.602d23d69b29fd83`, SHA-256
`602d23d69b29fd8316784d13559fa90bc1e8f4fa9378ea3ebd392fc76a97b540`.
Five-tool catalogue SHA-256:
`6bb5966e9158b0302dae6e1ded15a753c8f04e62553034a90b0a83408b38d1e5`.

On Linux, 2026-09-04, `python3 -B -m pytest -q` passed **54 tests** on this
payload. These include read-only package/sync and Markdown link validation.
All eight skills passed the skill format validator on this payload. README preserves its original
second-level section order and contains the updated Mermaid architecture diagram.
The PR workflow runs only for requests targeting `dev` or `main`.

Coverage includes five input/output schemas, structured/text result parity,
original requests, advisory governance without execution gates, real Markdown,
metadata-only SQLite, one newest-first pipeline, immutable ordinary reports,
Unicode cursors, catalogue snapshots, task isolation, concurrent writes, exact
retries, crash recovery, safe errors and retention. Multipart tests publish more
than 16 MB without truncation, including restart, rejected gaps, metadata changes,
disk failure and cleanup. Short report allocation handles collisions without
replacement. Every native profile contains its specialist role instructions and names required
skills. Codex loads those skills normally; their contents are not copied into
profiles. Assignments contain no manual plugin-installation paths. Report examples
are ordinary declared skill references, selected only when relevant.

Native-context tests cover missing/malformed metadata, unknown/conflicting parents,
child and nested inheritance, automatic pipeline selection, restart, concurrent
creation, one task per coordinator thread, cross-task report denial, absence of
public task/thread selectors and cleanup of bindings/creation receipts. These
prove storage behavior, not infallible future model decisions.

## Observe first, then implement

Before enabling automatic task selection, passive ingress logging on candidate
`da94c9d187bf4cd8` verified real metadata in ordinary CLI 0.153.0 and actual Desktop.
Each completed a read-only FAQ/source comparison with one native explorer and
nine successful task operations. Coordinator calls supplied their own thread and
no parent. Worker calls supplied a distinct thread and the exact coordinator as
parent. Each worker's first report write succeeded; neither run had MCP errors or
mutation replays. Both explorers received full profiles in native developer
instructions and made no plugin-file reads. The CLI exited with status zero;
Desktop was stopped after its visible final result.

Only after those observations was thread-bound execution enabled. No webhook,
Codex database lookup, model-authored task selector or latest-task fallback was
introduced. The [MCP review](project/mcp-contract-review.md) links the exact official
Codex source and explains the private metadata allowlist.

## Multistage real-host observations

Real execution uses only the isolated `.cortex-dev` candidate: ordinary Codex in
the exact `cortex-v12-smoke` tmux session, or actual Desktop with a disposable
Electron profile. The transport exposes events and delivers literal input; the
LLM verifier makes acceptance decisions. No stable plugin/configuration changes
are authorized by these checks.

A substantial Northline Studio landing-page trial on `1584605ac6b21c31` uses an
ordinary product prompt referring to a product brief. Parallel native explorer
and UX discovery both inherited the task automatically and published on their
first attempts. The frontend worker selected the current pipeline and only the
two relevant discovery reports. All three full native profiles were attached;
no plugin-file reads were observed. The coordinator used previews and native
waits, without project-file reads. Midwork EUR pricing and editable form
confirmation requirements updated the same pipeline and reached the active worker.

That trial is **not a clean acceptance run**: the frontend made two invalid native
patches, each deleting and adding the same path in one call. The documentation worker also made two oversized page requests, rejected with
the precise allowed range and correction. No mutation replay was observed.
The current payload strengthens native patch discipline and recommends default
read pages with cursors. Normal loading of the documentation-sync skill was
observed and is permitted; it is not installation exploration. Independent QA
verified wide-screen interactions, and headless Chrome produced actual 390×844
and 1440×900 screenshots. Phone interaction automation remained unavailable.
The older trial cannot qualify the newer payload. Final CLI/Desktop qualification
remain pending. The multistage task subsequently reached its visible final result,
including localization polish and synchronized documentation; ordinary CLI exited
with status zero. Its six workers all succeeded on their first report publication.
Across 61 task operations there were 59 successes, two argument rejections and
zero mutation replays. This remains a non-clean exploratory result.

Earlier exploratory trials also remain non-qualifying: one acknowledged mutation
replay; coordinator over-reading and direct project commands; missing assignment
references before automatic binding; preview-length rejections before the half-limit
writing target; and worker self-reading of profiles before native attachment.
One earlier post-compaction recap skipped mandatory catalogue/pipeline refresh;
the final-candidate CLI check below subsequently verified the required rereads.
Question UI trials never established success, and the user explicitly withdrew
that requirement. Questions use ordinary detailed chat text.

## Earlier routing and recovery checks

On `748fef4b4158b9f5`, a fresh ordinary CLI task uses native role profiles and
normal skill loading. The explorer loaded its declared shared skills, report
catalogue and only the investigation example. The technical writer inherited the
same task, selected relevant evidence, edited only the FAQ and published its
report. No profile or implementation-file investigation was observed.

This run is not a clean global conformance result: the coordinator used Romanian
for a Russian request and made one JavaScript syntax error before MCP dispatch.
The corrected call reached MCP once; this is not a storage replay. Independent
review completed with no FAQ mismatch. All 22 MCP operations succeeded without
replay. After real `/compact`, the coordinator fetched fresh catalogue previews
and the current pipeline before its Russian recap, keeping the same task. The
ordinary CLI exited with status zero.

Actual Desktop then completed a read-only FAQ/source check on that exact unchanged
payload: ten MCP operations succeeded, with zero MCP errors or replays. The native
explorer received its complete role instructions, loaded declared skills and one
relevant report example, inherited the coordinator task and published on its first
attempt. The coordinator read only its skill, previews and the current pipeline.
The visible final answer was in Russian; the disposable Desktop was stopped.
The worker also published a pipeline edition, which the coordinator reread.
These observations establish the changed routing, profile and skill behavior in
both hosts; the noted CLI language/wrapper faults prevent claiming a clean global
conformance pair.

## Current candidate qualification

The user requested completion after the earlier partial result. Language selection
now uses the user's own prose across commentary and recovery. Pipeline/governance
ownership is explicit. Host code-wrapper guidance is both in the normal skill and
in the live Markdown-field description, and an added stdio test preserves literal
code, quotes, backslashes and Unicode across restart.

Exploratory candidate `4239176937201189` still produced two pre-dispatch JavaScript
syntax errors in an explorer; it was interrupted. Candidate `1c7f44eb99b4bdfe`
then passed both first discovery publications but the frontend submitted multiple
native patch operations for one existing page; it was interrupted. The selected
frontend native profile now directly requires one update per existing path. Neither
interrupted run is qualified. A fresh complete product scenario and real Desktop
check on `602d23d69b29fd83` are in progress.

## Storage inspection

A read-only audit of eight stopped isolated stores checked **84 indexed reports**:
all current digests and owner-private permissions matched, SQLite integrity was
okay, and there were no foreign-key violations, unindexed files, duplicate
pipelines or pending publication/deletion intents. Historical formats 1–3 were
inspected read-only; current storage is format 4. No compatibility reader or
migration is shipped. The completed multistage format-4 store passed the same audit;
both final-candidate stores passed after their hosts stopped. All six final
native thread bindings had consistent parent/task relationships. Stable Codex configuration and installed plugin manifest hashes
matched the pre-task baseline at the latest comparison.

## Remaining limits

A clean full multistage run on the final payload remains unverified. A full
harvest/harvest-refresh census,
taskless clear through a native worker, native worker compaction, all 22 specialist
workflows and explicit Terra/Sol routing have not been exercised end to end.
Large multipart behavior is source/stdio evidence, not a model-authored enormous
report in both hosts. The macOS/Python-version CI matrix has not run locally.
Mermaid was reviewed as source, not rendered through a dedicated engine.
Same-user hostile filesystem races and hardware power loss are outside these
checks. Reads verify full-file integrity in bounded memory, so their time cost
grows with document size. See [storage](project/storage.md) and
[security](../SECURITY.md).
