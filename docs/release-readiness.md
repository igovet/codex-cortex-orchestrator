# 1.15.7: explicit graph discovery and native worker updates

Candidate `1.15.7+codex.sha256.03279e8f757a5b4d` restores explicit Codebase Memory
discovery in every worker and keeps all progress, questions, blockers and verification
updates on the native parent/subagent channel. The seven MCP operations, storage
format and 22 specialist profiles remain unchanged. Full payload SHA-256:
`03279e8f757a5b4d4ebb93d8f39a698a153505e3f6d0042723a0dcba4046441b`.

Stamp, package validation, source-only sync and 56 focused tests passed sequentially;
the complete suite passed **241 tests in 10.67 seconds**. Generated profiles retain
the existing size bound. Local documentation links and `git diff --check` passed.
Earlier source attempts caught an oversized profile and two changed prose assertions;
the final candidate passes the unchanged structural assertions and size limit.

The CLI helper disables Codebase Memory unless `start --codebase-memory` is used.
No-call observations from disabled-server or README-only scenarios do not establish
a graph-routing failure. The first enabled CLI pilot on `fc956615a02d530d` used an unindexed workspace:
the worker discovered Codebase Memory, paginated the project list, then used the
permitted source fallback and returned one native result. No app task messages were
sent. It completed in 340.379 seconds with 96 host calls and 12 successful MCP events,
but strict audit rejected the coordinator's unsolicited reminder and status probe
after waiting. Complete calls/events and the audit were retained before exit-zero
shutdown. This pilot is fallback evidence, not successful graph qualification.

A second CLI pilot on `fc956615a02d530d` used an indexed workspace and exercised
architecture, symbol lookup, four source snippets, caller tracing and coverage before
implementation. It completed in 304.75 seconds with 92 calls, 11 successful MCP events
and one native worker result. Strict audit rejected a combined provider-description
dump that was truncated and an initial coordinator project read. Complete calls/events
and audit were retained before exit-zero shutdown. No Desktop run was spent on this
payload. The final candidate makes discovery explicitly names-only before selected
contracts and prohibits whole-provider or combined-provider description dumps.

The next CLI pilot on `d58fd5d4ef5d3158` exposed a skill-loading-order gap:
the worker discovered tools before eventually loading its skill. Spawn message
arguments are opaque encrypted values in the retained host calls, so omission of
the exact skill token from the assignment is not established. The worker later
used the matching graph, but strict audit retained initial catalogue truncation,
a forbidden cache access, coordinator project work/status probing and a recovered
pipeline patch error. The run completed in 378.219 seconds with 125 calls and 11
successful MCP events; all tails and audit were retained before exit-zero cleanup.
The final candidate now places the exact skill-token and complete-loading requirement
near the start of coordinator guidance as well as in its assignment checklist.

The final unchanged payload ran consecutively on real CLI and Desktop with
Luna/high coordinators and Luna/medium native debuggers. Both workers loaded their
complete skills before catalogue discovery (the final skill marker was observed),
used the exact matching workspace index before filesystem code discovery, identified
the defective function and public call paths, and returned exactly one native result.
Neither run invoked an app task-messaging tool; progress remained native commentary.
This does not establish a separately dispatched native update/message operation,
because neither worker used one in these scenarios.

| Host | Host calls | Cortex MCP events | Graph calls | Hook actions | Total tokens | Seconds | Audit |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| CLI | 91 | 13 | 8 | 31 | 1,687,946 | 323.320 | Rejected: coordinator project read and two status probes |
| Desktop | 101 | 13 | 9 | 34 | 1,951,964 | 268.471 | Passed |

Both runs had no MCP errors, hook errors, host-tool errors, truncation or open
command sessions/cells. Independent verification passed the four existing tests,
nine entry-point reproductions per workspace, unchanged tracked-file checks,
SQLite integrity and the single physical pipeline. The generated diagnosis files
identify the faulty discount formula and all three public entry points. Desktop
explicitly documents both single-unit and multi-unit oversized discounts; CLI's
oversized-discount prose omits its chosen quantity and must not be read as proof
that every oversized discount already clamps to zero.

**Strict consecutive CLI/Desktop qualification remains unverified.** CLI's three
coordinator policy flags remain. Manual review also retains Desktop's missing
`check_index_coverage` call: its graph use is proven, but full coverage-protocol
compliance is not. These limits do not erase the observed skill-loading order,
graph calls or absence of app messages. Forced compaction, unavailable native
messaging and an actual blocker requiring an immediate native message were not tested.

Every host call was reviewed; complete calls/events and audits were retained before
cleanup. CLI exited zero and its exact session was stopped. Desktop submission
visibly matched the prepared composer and produced one new task receipt; its X11
desktop-property warning did not prevent delivery. Only the isolated process,
profile and temporary observation streams were removed. The stable plugin and
user settings were not changed.

# English-only workers from the first response — focused checks passed

Candidate `1.15.6+codex.sha256.879d600bfb9e3966` preserves semantic version 1.15.6,
seven MCP operations and all 22 specialist profiles. Its full payload SHA-256 is
`879d600bfb9e3966385113b754b8f27400e4a3f13603fc8b0c4e5b119355bbe2`; the catalogue
digest remains `f84b501e716b3990ba8308c428608061534b2dbc7a68d9dff177bf12b866be66`.

The shared worker guidance now requires English for reasoning and every
communication phase, including initial skill-loading commentary, progress,
questions, handoffs and recovery. Assignments state the rule before skill loading.
Only the coordinator follows the user's response language. Exact quoted source
text and required product language remain intact. Runtime code, tool schemas and
specialist routing are unchanged.

Stamp, package validation, source-only sync, 27 package tests and the complete
**241-test suite (10.82 seconds)** passed sequentially. All 24 changed skills passed
validation; generated profiles match their shared source. Documentation links and
`git diff --check` passed.

Two ordinary real-host scenarios used Russian requests for short English README
documentation on this unchanged payload. All nine worker messages were English,
including the first commentary before any tool call in each run. All seven
coordinator messages were Russian. Each worker published one report and delivered
one native result, with no cross-task message. The README examples printed `12`
and `7`; protected-file hashes, report hashes, SQLite integrity and the single
physical pipeline passed independent checks. Coordinators used Luna/high; the CLI
worker used Luna/high and the Desktop worker used Luna/medium.

| Host | Host calls | MCP events | Hook actions | Worker messages | Coordinator messages | Total tokens | Native task seconds | Audit |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| CLI | 65 | 13 | 30 | 6 English | 4 Russian | 1,005,464 | 254.836 | Report-reference flag retained |
| Desktop | 66 | 13 | 31 | 3 English | 3 Russian | 1,055,264 | 214.416 | Passed |

**Strict consecutive CLI/Desktop qualification remains unverified.** The CLI audit
reported `worker_final_with_unobserved_report` because the handoff cited both its
published worker report and the coordinator's predecessor pipeline. The retained
publication receipts confirm both references; the audit requires every extracted
report reference to belong to the worker. The flag was preserved rather than
treated as a clean audit. Manual review also retained an unnecessary CLI catalogue
lookup and no-op Git comparison. These observer and call-economy issues are outside
this language change. Desktop's audit had no errors, policy flags, truncation or
open sessions.

Complete current calls, events and audits were retained before shutdown. The CLI
exit-zero marker was captured; only the owned CLI/Desktop sessions and temporary
observation streams were removed. Desktop used a disposable profile and an exact
submission receipt; its X11 desktop-property warning did not prevent submission.
The stable plugin, user configuration and referenced user task were not changed.
The live checks establish observable message language; forced compaction and
unexposed internal reasoning were not verified.

## Previous response language and native handoff — focused checks passed

Candidate `1.15.6+codex.sha256.cc594f16f07f34cf` preserves semantic version 1.15.6,
seven MCP operations and all 22 specialist profiles. Its full payload SHA-256 is
`cc594f16f07f34cf537fb0b30854569c6b54c0e4568d88895aab52822f94cb88`; the unchanged catalogue
digest is `f84b501e716b3990ba8308c428608061534b2dbc7a68d9dff177bf12b866be66`.

The guidance separates the user's response language from English internal reports,
forwarded agent messages and requested product language. It carries that choice
through progress, final answers and context recovery. The shared worker protocol
explicitly returns results through the native final response without a duplicate
cross-task message. Runtime storage and tool contracts are unchanged.

Stamp, package validation, source-only sync, 27 package tests and the complete
**241-test suite (10.79 seconds)** passed sequentially. All 25 changed skills passed
skill validation; generated profiles match their shared source. Documentation links
and `git diff --check` passed.

Four ordinary real-host runs on the same unchanged payload used Russian requests
for English README documentation. All 15 coordinator messages, including four final
answers, remained Russian. Every worker returned one English native final response;
no run called cross-task messaging tools. Each executable example and protected-file
hash check passed. Report hashes, SQLite integrity and the single pipeline were
checked independently. Coordinators used Luna/high; workers used Luna/medium except
the second CLI worker, which used Luna/high.

**Strict consecutive CLI/Desktop qualification remains unverified.** Two individual
audits passed, but the execution order below did not produce a consecutive successful
pair. The failed runs remain part of the evidence; this focused change does not claim
to fix unrelated waiting or patch-construction behavior.

| Run order | Host calls | Recorded MCP events | Recorded hook actions | Total tokens | Native task seconds | Strict audit |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| CLI 1 | 72 | 12 | 32 | 965,895 | 211.468 | Passed; recovered missing-README read retained |
| Desktop 1 | 86 | 13 | 34 | 1,619,133 | 261.287 | Rejected: wrapper syntax, draft patch and status probe |
| Desktop 2 | 63 | 13 | 28 | 1,088,293 | 205.291 | Passed |
| CLI 2 | 75 | 13 | 32 | 1,181,720 | 236.629 | Rejected: status probe after wait |

The first Desktop run also produced overbroad wording about missing value fields;
its successful example does not establish complete documentation accuracy. Its final
usage was read after completion; event counts describe the retained audit snapshot.
The second Desktop audit had no tool errors, policy flags, truncation or open
sessions. The second CLI audit had no tool errors but retained its coordinator's
unnecessary status probe; manual review also found an unnecessary catalogue lookup
after the worker had supplied a report reference.

Complete current call/event tails and audits were retained before session shutdown.
CLI exit-zero markers were captured, and only the owned CLI/Desktop sessions and
temporary observation streams were removed. Real Desktop used disposable profiles
and exact-window submission receipts. Its X11 desktop-property warning did not
prevent submission. The stable plugin, configuration and user task were not changed.
Forced compaction, explicit response-language switching and exhaustive multilingual
coverage were not exercised by these focused scenarios.

## Previous project-local SQLite candidate — CLI/Desktop qualification passed

Candidate `1.15.6+codex.sha256.4b26dcd06eb65e14` preserves semantic version 1.15.6,
seven MCP operations and all 22 specialist profiles. The full payload SHA-256 is
`4b26dcd06eb65e14f7c074e343c3fda9b7da94997ad7e1d0bb20b11669f9b375`; the catalogue digest is
`f84b501e716b3990ba8308c428608061534b2dbc7a68d9dff177bf12b866be66`.

Cortex metadata now lives only in `<project>/.codex/cortex/cortex.sqlite3`, selected
from validated native thread and parent identity. Different projects use independent
SQLite files; tasks in one project still share SQLite serialization. No global-store
or environment override fallback is available. The explicit offline splitter keeps
legacy SQLite and Markdown unchanged and verifies a private backup before publishing
only the selected project’s metadata. Stable user data has not been migrated.

Stamp, package validation and source-only sync passed sequentially, followed by
**128 focused tests** and **241 total tests in 10.76 seconds**. Rejected unsupported
and foreign-project databases retain their bytes and journal mode. Independent
process tests prove project B can write while project A is locked. Same-project
concurrent writes, native routing, missing source/index behavior, retention and
archive export are covered. `git diff --check` and local documentation link checks
passed. The same unchanged payload completed consecutive ordinary CLI and actual
Desktop runs on 2026-09-06, with Luna/high coordinators and Luna/medium technical
writers. Both Cortex audits exited zero. Complete host-call reviews found no MCP
or hook failures, Cortex protocol violations, truncation or open command sessions.

| Host | Host calls | MCP events | Hook actions | Total tokens | Cached input | Native task seconds |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| CLI | 66 | 11 | 31 | 922,977 | 826,880 | 196.026 |
| Desktop | 59 | 14 | 27 | 1,114,046 | 1,000,704 | 192.145 |

The CLI trace retains a project example import error and the successful corrected
invocation; it is not an error-free product run. Desktop had no host-tool errors.
Its X11 focus command emitted a desktop-property warning but returned success, and
the exact-window submission produced one new task receipt. Independent examples,
contract checks, protected-file hashes, SQLite integrity and foreign keys passed
on both hosts. Each retained database contains one task and only its own project’s
coordinator/child bindings. All declared artifact hashes and final report hashes
match, all drafts are published, and each archive has one newest-first pipeline.
CLI exited normally with its zero marker; Desktop and CLI observation sessions
were stopped and temporary streams removed while project stores were preserved.
The stable plugin, configuration and legacy shared database were not changed.
This qualifies the tested routing, documentation, report and hook path; it does
not claim universal task success or eliminate contention within one project.

A source-only hook benchmark ran 100 fresh `python3 -B` subprocesses per path.
All 300 exited zero with exact empty JSON responses, empty stderr and one private
observation each. P95 including startup was 45.716 ms inactive, 51.370 ms for active
tool receipts and 53.023 ms for deferred prompt capture. Active paths used a
synthetic native index and a project-local task; the inactive path created no store.
Whole-task percentage overhead remains unverified.

The first CLI run and its same-task resume passed all independent product and
archive checks, with no MCP or hook failures. Resume retained the task, parent/child
bindings and pipeline, archived the next request as source revision 2, and preserved
prior README and pipeline content. Their strict protocol audits were rejected:
the first run used a wrong draft path before correcting it, and both turns
shell-read fresh pipeline drafts. The resume also retained two recovered README
patch failures. Complete calls/events and audits were captured before both normal
exit-zero shutdowns. An additional false skill-read flag was corrected in the
isolated observer using a narrow static Python `-c` grammar; all 29 observer tests
pass, and replay preserves the genuine violations. The plugin payload was unchanged.

---

# Historical Format 11 v9 improvement candidate — bounded CLI/Desktop qualification passed

The source replacement preserves semantic version 1.15.6, seven MCP operations and
22 specialist profiles. It introduces compact instructions, task-isolated storage,
source/provenance metadata, an offline migration and local lifecycle hooks.

Candidate: `1.15.6+codex.sha256.0066c0266f853fd5`, full SHA-256
`0066c0266f853fd53a82e6b911d49eec83cd92c89d3f8cfcf1ae479e1a619d9a`.
Seven-tool catalogue digest:
`eb2aea4c29706e269f611d4565dfec3b8a9a881c716e90cf13eb4e2f7f07557e`.

The same five coordinator skills (orchestrator, content safety, context compaction,
communication and tool discipline) total 20,694 characters versus 68,825 in baseline:
a 69.93% reduction. The orchestrator file alone is 13,694 versus 49,860 (72.54%); the
representative complete backend worker skill is 10,211 versus 36,610 (72.11%), and the
shared worker protocol is 8,132 versus 34,282 (76.28%). These
counts include front matter and use identical decoded-Unicode/newline treatment;
conditional documentation, harvest, maintenance and rare references are excluded
from the coordinator comparison in both variants. Size reduction is not a task-quality claim.

Stamp, package validation and source-only sync passed sequentially. The focused suite
passed **69 tests**; the complete suite passed **208 tests in 8.90 seconds**. All 30
skills, profile generation and `git diff --check` passed.
The [verification map](project/verification.md)
links mandatory failure and recovery scenarios to their actual unit tests.

The unchanged candidate completed consecutive ordinary CLI and actual Desktop
documentation scenarios on 2026-09-06. Both coordinators used `gpt-5.6-luna` at
high effort; both native technical writers used Luna at medium effort. Full skill
loads, command receipts, bounded discovery, worker publication and coordinator
acceptance passed the complete host-call review. Both audits exited zero, with no
MCP or hook failures, Cortex protocol violations, truncated results or open command
sessions. Independent README examples and contract checks passed, and all protected
files retained their hashes.

Independent read-only audits of both final archives verified Format 11 SQLite
integrity and foreign keys, exact coordinator/child bindings, source revision 1,
all declared artifact hashes, owner-private report files and their indexed hashes.
Each archive has one physical pipeline with its newest section first and older
content below it; all three drafts are published, with no pending deletion or
unindexed report. All 107 local documentation links resolve.

| Host | Host calls | MCP events | Hook actions | Total tokens | Cached input | Native task seconds |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| CLI | 86 | 12 | 37 | 1,309,671 | 1,216,512 | 207.747 |
| Desktop | 70 | 14 | 30 | 1,314,999 | 1,208,320 | 155.101 |

The CLI trace retains two failed project README patch attempts and their subsequent
exact successful retry; these are recovered product errors, not an error-free run.
Desktop had no tool errors. CLI emitted its normal exit-zero marker, and both exact
isolated sessions were stopped after complete evidence capture. This qualifies the
tested instruction-loading, documentation, report and hook path; it does not prove
every task, recovery path or a general quality improvement. The 12-run comparison
below remains a separate earlier payload, not a repeated benchmark of v9.

On this exact candidate, 100 fresh Python subprocesses for each of three hook paths
all exited zero with checked output and private observation receipts. The measured
p95 including startup was 39.017 ms inactive, 47.452 ms for active tool receipts and
50.982 ms for active deferred prompt capture. This meets the 100 ms local-handler
target. Whole-task hook overhead at or below 5% is still unverified; internal handler
durations omit startup and cannot establish that result. Each active benchmark used
a fresh synthetic task and 100 distinct tool or turn identities; the inactive path
had no store. These are local subprocess measurements, separate from model-run time.

The earlier v5 qualification was rejected for incomplete initial command receipts and
truncated catalogue discovery; product checks passed, while recovery later stalled
after draft creation and was cancelled before lifecycle state was known. V6 completed
and stopped normally with product checks 7/7 and no MCP or hook failures, but protocol
qualification failed on the initial coordinator receipt and a recovered worker's
draft-report hash correction patch, which initially failed context verification before
repair. V7's CLI product checks passed 8/8 with complete receipts and no truncation,
but post-publication handoff lookup was rejected. Its exploratory Desktop run passed
product checks 8/8 and completed normally in 170.432 seconds using 1,686,903 tokens;
The trace contains 74 host calls, 13 successful MCP events and 30 successful hook
receipts; two catalogue searches were truncated across four wrapper/nested records and a status probe was
rejected. V8 was source-only and never live. V9 adds the concrete names-only
discovery example. The first v9 CLI smoke attempt omitted an explicit data store and
selected an incompatible pre-existing isolated store, so setup was invalid; its
product checks were 8/8 in 100.473 seconds using 582,992 tokens, but it recorded two
unsupported-storage MCP errors and 14 hook storage errors, followed by model
protocol errors. It is neither qualification nor a Format 11 runtime regression.
The helper's fresh-store and exact-resume changes passed the source checks above.
The successful final pair above used explicit fresh stores. The separate
three-configuration/four-scenario Luna pilot remains the frozen v4 comparison with
12/12 runs; all functional checks passed, with one baseline protocol pass and no
candidate protocol passes. The [comparison and retained measurements](project/quality-evaluation.md)
separate outcomes from protocol defects and exclude disclosed tuning/intervention
attempts. Its full-hooks payload is
`fd022bfc005647faab9ddde3ec5c493ea00773ce28b9389cf2ed355039796375`; its compact
counterpart differs only in the active-hook manifest and generated package version.
Three observer false flags were corrected in MAIN: two for safe literal quoted-Python
full-skill reads and one for exact Desktop escaped-underscore plus terminal-newline
source fidelity. Storage retains exact native bytes, and the observer guard verifies
delivery before accepting an exact archive hash. Quoted-heredoc AST validation rejects
shell expansion, with regression coverage added. V4 efficiency measurements and
historical host passes below do not qualify v9.
Immediate UserPromptSubmit publication
without a native message identity is deliberately deferred, and reused-worker
assignment-level stop coverage remains incomplete when the host omits that boundary.
No stable plugin installation or configuration has been changed.

---

# Historical candidate: complete skill loading — 2026-09-06

Candidate: `1.15.6+codex.sha256.fd0b4e63ad8eea97`, full SHA-256
`fd0b4e63ad8eea97cd5bd39ce893675db5e2b0cc6fbcdc2c91fff4a35bf99345`.
Seven-tool catalogue digest:
`999c3a0324d5b34e4c124542269fccdb2ea86267de9e0d46ff78bf26efb6739f`.

Actual workers repeatedly treated a successful 240-line range as the end of a
longer skill. Every generated worker skill now declares its exact line count and
ends with one standalone completion marker. The full shared profile remains intact;
there is no custom loader, new operation, profile or storage migration. The
coordinator's assignment prefix requires reading through that boundary before
catalogue discovery or project work.

Generation, stamp, package validation and source-only sync passed in sequence.
Official plugin validation and 23 skill validations passed. Focused checks passed
48/48. After the final observer correction, all 26 decision/audit checks passed;
the concluding full suite passed **138 tests in 72.33 seconds**. Documentation links
and `git diff --check` passed.

The same unchanged candidate completed consecutive ordinary code-reading/documentation
scenarios on actual CLI and Desktop. Both orchestration audits passed. All **64 CLI
calls / 11 MCP events** and **72 Desktop calls / 14 MCP events** were reviewed. The
CLI worker read the whole skill in one command; the Desktop worker continued from
lines 1–240 through 241–528. Both exposed the final marker before tool discovery,
used exact-workspace Codebase Memory evidence, wrote the correct requested example
and duplicate-ID explanation, and preserved both protected files by SHA-256.
No truncated discovery, TOML access or invalid report reference was observed.

Each worker transparently recorded a recovered JavaScript `store(undefined)` failure
after a successful MCP project listing. Native MCP receipts prove that those calls
succeeded; the failed consumer processing remains visible in the audit. The observer
now distinguishes these outcomes instead of labeling the MCP itself as failed.
Independent product checks and manual README review passed. The CLI exited zero;
both isolated sessions were stopped after evidence capture.

This qualifies the bounded marketplace-loading, retrieval, report and documentation
path, not every project or a general intelligence improvement. Same-role continuation
and fresh verification were observed on both hosts in the preceding candidate;
its exact Desktop steering failure remains disclosed below. Recovery of stranded
native `pending_init` slots is **not verified or fixed by this plugin**. The completed
comparison remains **12/12 trials**, with no proven significant gain. Stable user
installation and configuration were not changed.

---

# Historical marketplace candidate — 2026-09-06

Candidate: `1.15.6+codex.sha256.569e08207d9e2012`, full SHA-256
`569e08207d9e201270d93b446de5784c99ed0a8bd82fd049578d18079dd9eb65`.
Seven-tool catalogue digest:
`999c3a0324d5b34e4c124542269fccdb2ea86267de9e0d46ff78bf26efb6739f`.

Complete skill loading now precedes all catalogue discovery; dependent operations
cannot share the initial read call. The observer recognizes one reply to the exact
active worker that messaged its coordinator. Previous evidence, routing, report-ID
and lifecycle changes remain; no operations, profiles or storage migrations were added.

Generation, stamp, package validation and source-only sync passed in sequence.
Official plugin validation and validation of the 23 latest changed skills passed.
Focused checks: 45 passed. Full suite: **134 passed in 72.13 seconds**.
After observer-only corrections, 24 decision/audit checks also passed. Plugin
access is determined from patch targets rather than mentions in report prose;
concurrent report reads are correlated by retained literal references when available.
These corrections did not change the candidate payload.

Actual CLI completed the initial product task and generator follow-up: independent
initial checks passed 2/2, final original checks 2/2 and one-shot/stop-on-conflict
checks 2/2. The implementation context received an explicit new assignment, wrote
a new report, and a fresh verifier checked it. All five previously saved non-pipeline
Markdown documents retained their hashes. All 206 call records and 30 MCP events
were reviewed; no policy violation remained after correcting observer attribution.
Clean protocol qualification still failed: initial workers read only 240 skill lines
and used broad, truncated catalogue lookups; the follow-up verifier corrected one
wrapper syntax error. No invalid report dispatch, TOML access or wrong graph index
was observed. CLI exited zero and stopped. Stranded-slot recovery was not exercised.
Actual Desktop also passed initial and final product checks (2/2 each), plus the
generator checks (2/2), and preserved all four prior non-pipeline documents.
Same-role continuation and fresh verification were observed. All 222 calls and
38 MCP events were reviewed. Nine policy flags and 14 unsuccessful call records
remain, including partial skill loading, broad truncated searches and unnecessary
worker catalogue access; recorded diff exit 1 results are expected differences,
not product failures. The literal steering helper correctly rejected a changed
first character (ASCII A became U+0424), although the remaining requirements arrived
and the task completed; the message was not replayed. That transport attempt is not
a successful exact-delivery qualification. The isolated Desktop process was stopped.
Neither host qualifies this candidate as a clean protocol pass.
Stable user installation and configuration are unchanged.

---

# Historical capability-routing candidate — 2026-09-06

Candidate: `1.15.6+codex.sha256.40391f7f43ad3efa`, full SHA-256
`40391f7f43ad3efa0ec969f4020ece752094f09873d701f847af0d4cd0b5d7fb`.
Seven-tool catalogue digest:
`999c3a0324d5b34e4c124542269fccdb2ea86267de9e0d46ff78bf26efb6739f`.
Version 1.15.6, seven operations, 22 profiles and metadata-only SQLite are preserved.

This candidate requires deterministic comparison of the complete graph workspace
root before retrieval, retains evidence scope in the decision brief, and removes
the assumption that an omitted override means Luna. Model choice now starts with
an appropriate capable baseline and qualifies cheaper choices against quality.
Normal advertised skill loading and all previously approved decision improvements
remain. The observer also correlates actual server events with execution intervals
and detects stdout-only command wrappers.

Profile generation, stamp, package validation and source-only sync passed in
sequence. Focused package/decision checks passed (44 tests), followed by the full
suite: **133 tests in 72.31 seconds**. Actual CLI passed independent product checks (2/2), preserved the protected file
and exited zero. Review of all 133 calls and 17 MCP events rejected clean protocol
qualification: both workers batched incomplete skill loading with broad discovery,
truncating the catalogue. Exact subsequent lookups recovered needed schemas. One
missing-file search exited 1. No invalid report dispatch, TOML read or wrong graph
index was observed. A coordinator reply was incorrectly flagged as unsolicited;
the next observer revision fixes this with an exact-worker regression. No Desktop
run was spent on this rejected payload. Stable user installation
and configuration remain unchanged.

---

# Historical standard-loading candidate — 2026-09-06

Candidate: `1.15.6+codex.sha256.da559702a1acb07a`, full SHA-256
`da559702a1acb07a6e5d8867ca1d613c0036ac317bde451bf2d0f927f941b139`.
Seven-tool catalogue digest:
`999c3a0324d5b34e4c124542269fccdb2ea86267de9e0d46ff78bf26efb6739f`.
Semantic version remains 1.15.6, with 22 profiles and no storage migration.

Official documentation confirmed that reading an advertised SKILL.md and relevant
references is normal progressive loading. The user withdrew the absolute read ban.
The candidate supports this standard marketplace route without personal registration,
while keeping TOML and server-internal exploration forbidden. Every delegated role
receives the same short loading, complete-command-receipt and graph-first prefix,
including verification. Documentation may stay with the implementation owner; extra
specialists require a concrete evidence need. Acceptance must reach the user's
observable behavior. See the [source-backed review](project/orchestration-review.md).

Profile generation, stamp, marketplace validation and source-only sync passed in
sequence. The complete suite passed: **131 tests in 72.41 seconds**. After a separate observer
fix, all 20 decision/audit regressions passed, including the new missing-command-receipt
case; this observer-only edit did not change the installed payload. Stable installation
and configuration were not changed. Actual CLI passed the independent product grader and protected-file check, but
qualification was rejected: the implementer queried a previous fixture index. All
135 call records and 16 MCP events were retained and reviewed. A skipped invalid
regex guard was also incorrectly matched to the later successful report read by
the observer; interval-based matching now has a regression test. The CLI exited
zero and stopped. No Desktop run was spent on this rejected payload.

## Intermediate standard-loading candidate

`1.15.6+codex.sha256.b95024d903502e34` passed the product grader and protected-file
check on actual CLI. Full review of 118 call records and 16 MCP events still rejected
qualification: the verifier omitted graph-first discovery and two skill-read wrappers
exposed stdout without their command receipts. Four exploratory checks failed before
corrected checks passed; their records remain visible. A Git warning was a guarded,
unexecuted branch. The metadata orchestration audit passed, demonstrating why manual
review remains necessary. The CLI exited zero and was stopped before preparing the
corrected candidate. No Desktop run was spent on this already-rejected payload.

---

# Historical absolute-read-ban candidate — 2026-09-06

**Rejected historical candidate; requirement superseded.** The user later authorized
normal documented skill loading. The shortened comparison had 12 attempts; all completed.
All final products passed independent checks, but five runs had protocol violations.
The combined bundle showed no proven significant gain. See the
[decision and measurements](project/quality-evaluation.md#shortened-series-decision-2026-09-06).

Subsequent changes forbid every installed plugin/cache file read, including skills
and TOML, strengthen exact reference handoffs, and audit ineffective capacity retry
loops. All skills remain standard marketplace components. They do not fix missing
native skill attachment or stranded V2 residents inside Codex itself. The inspected
0.153.0 and 0.153.4 source confirms the [host limitation](project/host-compatibility.md).
Historical successful runs below used a now-forbidden filesystem instruction read
and do not qualify this stricter candidate. Stable installation remains unchanged.

Candidate: `1.15.6+codex.sha256.2f30e6d45800de64`, full SHA-256
`2f30e6d45800de64ce11fb48b0d35a3331568aec943cbcd62a1909d05f9bd782`.
Seven-tool catalogue digest:
`999c3a0324d5b34e4c124542269fccdb2ea86267de9e0d46ff78bf26efb6739f`.
Profile generation, stamp, marketplace validation and source-only sync passed in
sequence. The full suite passed: **131 tests in 72.15 seconds**. Local documentation
links and `git diff --check` passed. Required feature-census guidance is generated
inside its own marketplace skill; agents need no external reference-file read.

The actual CLI initialized this exact marketplace cache and catalogue, then failed
the ordinary product scenario: two specialist roles reported missing native skill
attachment and made no product changes. All 34 call records and eight MCP events
were reviewed. No plugin-file read or MCP error occurred. One truncated discovery
result appeared as two audit records (wrapper and nested call), and three worker
terminal results lacked reports. A follow-up and a fresh role retry did not change
the missing host capability. The coordinator accurately reported blocked work;
the original product files remained byte-identical. The audit failed, as required;
the CLI exited with code zero and its exact session was stopped. This is failure
evidence, not a successful functional qualification or capacity-recovery test.

Actual Desktop used the identical candidate: 73 call records and 14 MCP events.
The product implementation passed four independent post-run checks, but the run was
rejected: the implementer started project work without loading its full skill and
used Codebase Memory only afterwards. Fresh verification workers reported missing
skill attachment. Six host-failure records included a real Git invocation outside
Git, truncated discovery and a failed attempt to use the app messaging interface
for native coordination. The audit recorded 13 flags, including over-attribution of
the batched Git command to unrelated commands. No installed-plugin read occurred.
The coordinator disclosed the missing independent verification. Complete evidence
was saved before the isolated Desktop process and temporary artifacts were stopped.
Neither strict-host run proves capacity recovery or correct standard skill loading.

Earlier check counts and hashes below apply only to their named candidates.

---

# Continuation ownership and coordinator boundary correction — 2026-09-05

Candidate: `1.15.6+codex.sha256.6a789c1d6d2eb0ef`, full SHA-256
`6a789c1d6d2eb0ef4a33532c7b9df1fc08be43997cc3b9024d60505fc4abf219`.
The seven-tool catalogue digest is
`593ab64e820266404c2ad88a674b51390b5e2fd3c531e6b0de91fb33aaa429c9`.
Stable installation and configuration remain unchanged.

The language table no longer incorrectly suggests coordinator-authored synthesis.
Coordinator completion records acceptance in the pipeline and answers the user;
an additional saved synthesis is an optional worker artifact. Read descriptions
and instructions consistently limit coordinator evidence to opening report briefs.
Assignments now carry command-receipt requirements before the first worker skill read.

The continuation audit requires the latest successful publication from the exact
worker. Its earlier published report references may accompany that result; unknown,
foreign or old-only references fail. This replaces an unnecessarily strict reference
count rule. Restart snapshots preserve publication ownership without replaying old
calls, and invalid final handoffs cannot restore completed ownership. The historical
failed runs below retain their actual, then-current audit results.

Source checks: **127 tests passed** in 71.41 seconds; package validation, source-only
sync and skill validation passed. No storage migration or public operation was added.
The comparative outcome series is underway; completed attempts are recorded in
[the trial aggregates](project/quality-evaluation-results.json). The full comparison
and replication remain incomplete. Source checks do not establish a percentage
quality or efficiency gain.

Real CLI passed both initial investigation and same-task continuation after restart
on this unchanged payload. Initial phase: 71 call records and 12 MCP events; resumed
phase: 34 records (including the retained-assignment snapshot) and 9 MCP events.
Both audits had zero host failures, Cortex errors, role violations, open sessions
or open cells. Every call was reviewed. Codebase Memory provided initial graph
search, snippets, tracing and coverage. The resumed explorer reused retained context,
checked current source and boundary cases, then published a new immutable report.
All original sources and three initial immutable documents remained byte-identical;
there was one canonical pipeline. Both CLI exits carried the explicit zero marker.

Actual Desktop then passed the initial investigation and explicit same-worker
clarification on the identical candidate: 122 call records and 22 MCP events across
two completed turns. Every call was reviewed; host failures, Cortex errors, role
violations, open sessions and open cells were all zero. The worker used the existing
Codebase Memory graph before source checks, retained context for the clarification,
and published a second report without changing earlier documents or source files.
The coordinator read opening briefs and authored only the pipeline. Exact parent
follow-up ownership was observed on both real hosts, including CLI after restart.

Desktop's prepared composer and the retained task's empty composer were inspected.
One initial submission and one clarification produced their exact native receipts;
only the observed editor-added paragraph newline is allowed for the latter. A short
focus-settling delay and slower synthetic typing preceded the successful repeat of
the earlier failed ASCII fixture. This does not qualify arbitrary keyboard layouts
or non-ASCII follow-up input. Desktop resume was not exercised.

These consecutive CLI/Desktop scenarios qualify the bounded graph-investigation,
report-read boundary and continuation behavior, not general task intelligence.
Both fixture databases passed integrity and foreign-key checks. Report files were
private, no unpublished drafts remained, and both helpers stopped their exact test
processes. Raw logs, reports and screenshots remain private outside the repository.

# Decision quality and Codebase Memory candidate — 2026-09-05

Candidate: `1.15.6+codex.sha256.3d45a654e1b78c8a`, full SHA-256
`3d45a654e1b78c8a14b025c11e8819e2144b95694195a465ed0b556953da22e0`.
This is a development candidate, not a newly qualified release. The stable plugin
and user configuration were not updated. Earlier qualifications below describe
only their recorded hashes, not this candidate.

The candidate adds selected opening decision briefs, uncertainty-driven assignments,
explicit same-role continuation with new immutable reports, fresh verification
contexts, observed-bottleneck model routing, and graph-first Codebase Memory guidance
in all 22 profiles. The seven-operation storage contract and semantic version remain
unchanged. No storage migration or public workflow operation was added.

Source validation: **126 tests passed** in 71.25 seconds; package validation,
source-only sync and diff whitespace checks passed. A separate collection command
created Python bytecode; package validation correctly rejected that generated cache.
After removing only the bytecode, package and sync checks passed on the same hash.
Tests cover continuation authorization and ownership, per-assignment report identity,
resume metadata, coordinator read boundaries, Desktop steering receipts and evaluation
record integrity. These tests do not establish native-host compliance or quality gain.

A baseline CLI pilot solved the retry-dedup fixture and preserved the protected file,
but its protocol audit failed. Earlier changed CLI pilots exercised graph discovery,
snippets and coverage, then implementation-context reuse on a user correction.
The latter also reused a verifier and emitted two report identifiers in a worker
final; it was rejected. The instructions and observer now explicitly reject these
cases. Another pilot bypassed graph discovery for a named source file and failed
bootstrap/command-receipt checks; it was also rejected. Complete calls, events and
audits were retained privately and each run was followed to completion before cleanup.

On the final candidate, real CLI completed the threshold caller investigation and
an optional-inclusive design follow-up with two fresh roles. The architect used
`list_projects`, missing-project indexing, graph search, exact snippets, inbound
tracing and coverage before a bounded source check. The coordinator consumed
opening decision evidence, but also read an ordinary continuation page and authored
synthesis reports outside its allowed role. One report patch initially failed;
command wrappers omitted terminal receipts. The audit rejected the run (142 call
records, 29 MCP events). All original fixture files remained byte-identical, no
sessions or execution cells remained open, and the exact CLI exited with code zero.
This confirms graph-route use, not a clean orchestration qualification. CLI resume
on this final payload has not been exercised. The Desktop fixture was indexed
separately before launch to exercise the existing-index route; this is not an
identical-initial-state comparative trial.

Actual Desktop on the same payload completed the initial graph investigation and
one clarification in the same explorer context (102 call records, 23 MCP events).
The observer resolved the successful parent follow-up to the exact completed child;
the child published a second immutable report and preserved all fixture source files.
The coordinator used opening briefs. The initial Cortex audit had no role violations,
but the full clarification audit rejected a worker final containing multiple report
identifiers. Four initial host failures included stdout-only wrappers and an explained
source command error; a clean Cortex-only audit is not a clean all-call qualification.
Both native task-complete receipts were observed, with no open sessions or cells.
The disposable Desktop process was stopped through its helper.

Desktop steering transport refused an exact-delivery acknowledgment: native keyboard
input changed the initial Latin character to a Cyrillic lookalike. The message had
already arrived in the correct task, so it was not replayed. The observer now accepts
only the separately observed single editor-added paragraph newline; it still rejects
changed characters. The 15 focused decision/transport tests passed after this narrow
developer-tool correction. Automated Desktop steering remains unqualified; the
failure is not an authorization to accept approximate text. Desktop resume was not run.
Consecutive clean CLI/Desktop parity on this candidate has therefore **not** been
established. Raw logs, reports and screenshots remain outside the repository.

The [comparative evaluation procedure](project/quality-evaluation.md) and executable
12-task fixtures are implemented. The full 216-trial baseline/ablation/combined
comparison and replication have **not** been run. Neither the proposed 20% benefit
nor a general increase in reasoning quality is demonstrated by these development
checks. Model preferences remain provisional.

# Native steering qualification — 2026-09-05

Qualified payload: `1.15.6+codex.sha256.cc786ae2fbd04cf1`, full SHA-256
`cc786ae2fbd04cf1e9c29cfb34cf721de6ad6b8663f2d05f809baf2bee158698`.
The ordinary installed plugin and user configuration were not updated. Only the
isolated dev candidate was prepared through the repository launchers.

The same unchanged payload passed consecutive real interactive CLI continuation
and actual Desktop runs. CLI resumed the existing CSV task after two live steering
messages, then added an inclusive decimal threshold combined with currency filtering,
whole-input validation, atomic output and preservation constraints. Four new native
workers exercised implementation, independent rejection, bounded correction and
fresh verification. Desktop ran the original ten-requirement CSV brief from an
untouched fixture, using implementation and independent verification workers.
These were ordinary product prompts, without orchestration-test instructions.

| Qualified phase | New workers | MCP events including initialization | Call records | Final Cortex audit |
| --- | ---: | ---: | ---: | --- |
| Real CLI after restart and further steering | 4 | 32 | 238 | passed |
| Actual Desktop, same payload | 2 | 18 | 108 | passed |

Every coordinator and worker reached native task completion. The full call records
were reviewed; Cortex contract errors, report-write failures, unexplained replays,
truncated orchestration output and role/order violations were absent. Project-code
and test-command failures were assessed separately and handled by the live task;
this does not claim that every project-development invocation succeeded.
CLI exited with the explicit zero marker. The exact CLI session and disposable
Desktop process were stopped through their helpers. Desktop's prepared composer
was visually confirmed before one submission with a new task receipt. Passive
initialization matched the candidate path and seven-tool catalogue on both hosts.

The CLI archive contains the original source plus three separate steering messages,
including the post-restart request. Every stored source matched the actual native
UserMessage text and its digest. Desktop's original source also matched its native
receipt; native editor formatting was checked against the prepared prompt.
Desktop steering and Desktop resume were not separately exercised in this pass.

Storage checks passed SQLite integrity, foreign keys, report file hashes, sizes and
permissions, and absence of unpublished drafts. CLI retained one task, 14 documents,
one pipeline and 24 editions across both phases. Desktop retained one task, five
documents, one pipeline and six editions. The original CSV and protected user note
remained byte-identical in both fixtures. Raw evidence and screenshots remain private
outside the repository.

Source checks on this payload: **110 tests passed**, package/marketplace validation,
source-only sync check and Markdown links passed. Tests include queued corrections,
exact Unicode/whitespace, restart, duplicate native receipts, delivery replay with
new input, partial host records, source replacement/conflicting identities, disk-failure
rollback, literal credential redaction, size rejection without truncation and retention.

An earlier run on `f942d09fa00a961c` was rejected for one worker's unnecessary
one-character reread of an immutable report before publication. Its complete tails
and audit were retained and the run was followed through completion. The final
contract and all 22 shared profiles explicitly exclude such probes. A separate
observer defect incorrectly classified real user steering delivery after a wait;
typed native user-input events now provide the narrowly scoped audit evidence.

Steering capture commits on the next successful coordinator task operation after
creation, except replaying create_task. It is not a background hook. The archive
preserves source text independently of the pipeline; semantic interpretation and
acceptance still belong to the coordinator. Missing or oversized source fails closed
rather than silently truncating requirements. Only explicit literal credential
redactions change captured steering text. Attachments are outside this text reader.

# Complex CLI/Desktop qualification — 2026-09-05

Qualified payload: `1.15.6+codex.sha256.e5e7f2252bcdf325`, full SHA-256
`e5e7f2252bcdf325037dc861f65e9f42f038adb0607924a6094cda44f475cd2a`.
Semantic version remains 1.15.6; the ordinary installed plugin was not updated.

The unchanged isolated candidate passed consecutive real CLI, resumed CLI and
Desktop runs. The initial ordinary Russian product prompt had ten requirements:
offline CSV reconciliation, exact decimal arithmetic, Unicode/multiline fields,
strict duplicates and validation, atomic output, error preservation, dry-run,
protected existing edits, a specific test command, documentation and independent
verification. The resumed task added currency filtering while retaining all prior
validation and preservation requirements. No orchestration-test instructions were
inserted into the product workload.

| Real-host phase | Native workers | MCP events, including initialization | Host-call records | Cortex audit |
| --- | ---: | ---: | ---: | --- |
| CLI initial task | 2 | 16 | 115 | passed |
| Same CLI thread after restart and new requirement | 2 | 16 | 104 | passed |
| Actual Desktop initial task with corrective work and re-verification | 4 | 28 | 174 | passed |

All coordinators and workers produced native task-complete receipts. Both CLI
phases ended with the explicit exit-zero marker. Desktop's prepared composer was
visually confirmed and submitted once to its exact isolated window; completion
was verified from native task and MCP receipts, not inferred from the UI. Every
host-call record was reviewed. Cortex calls, draft patches, publication, selective
report reads, role boundaries and session closure passed without Cortex errors,
truncation, unexplained write replays or post-publication worker calls. Project
implementation and command diagnostics were assessed separately from Cortex,
as required; this is not a claim that every project-development tool call succeeded.

CLI retained one task, seven documents, one canonical pipeline and ten editions
after continuation. Desktop retained one task, seven documents, one pipeline and
eight editions. Both passed SQLite integrity and foreign-key checks, stored-file
size/hash/permission checks, and absence of unpublished drafts or pending deletes.
Input CSV and the pre-existing user note remained byte-identical to the initial
fixture. Exact test sessions and the disposable Desktop process were stopped.
Private full calls, events and screenshots remain outside the repository.

The original request now comes from the host's typed user-message receipt, scoped
to the current thread and canonical project. It is no longer retyped by the model.
The observer verifies its stored digest against independently delivered input;
Desktop-only rich-text serialization is accepted only with observed provenance.
The source reader uses the tested host state/session format and fails closed if
that source cannot be established. Literal credential redaction and source
isolation are unit-tested; no real credentials were used in live workloads.

All 101 source tests passed. Package validation, source-only synchronization,
generated profiles, relative documentation links and diff checks passed.
All 22 specialist profiles and seven MCP operations remain packaged. Hooks,
custom global agent registration and stable-user installation changes were not
used. The earlier sections below are historical evidence, not the current result.

# Rejected intermediate complex candidates — 2026-09-05

The previous marketplace checks below cover their named simple scenarios only.
The new offline reconciliation scenario has ten simultaneous requirements,
independent verification, immutable input and existing output preservation,
Unicode CSV, exact decimal validation, and a protected pre-existing user edit.

The first complex CLI run completed with 20 MCP events and no MCP errors, but
failed qualification: task creation included a host environment envelope and
several instruction-read wrappers omitted structured command receipts. The
second candidate improved instruction receipts but still added a synthetic
skill envelope to the saved task. These runs are rejected, not success evidence.
The observer checks task-text fidelity without exposing content, and the
coordinator now distinguishes user prose from injected instruction envelopes.
The third candidate preserved the exact request and published the implementation
report, but still omitted the first skill-read command receipt. The assignment
now specifies whole-file instruction loading and structured result forwarding;
the report protocol appears before ancillary execution guidance in every profile.
The fourth run exposed delegation after editing an unpublished pipeline draft.
The working sequence had placed discovery before pipeline publication; that
ordering is corrected, and the observer now rejects premature delegation.
Candidate `35d68aa0c27151a5` passed complex CLI and restart/continuation checks,
but Desktop exposed a shortened report reference and premature worker final.
A later CLI run still omitted an input-preservation sentence from copied user text.
The current implementation removes model transcription from task creation: it
captures typed native user input within the current host thread/project boundary.
It also validates reference shape before MCP dispatch and keeps missing-reference
questions inside an unfinished assignment. Source tests cover these changes.
No ordinary installed plugin was changed during this work. These intermediate candidates do not establish final qualification.

# Marketplace-only orchestration — 2026-09-05

The follow-up ordinary Desktop session still stopped before its first native worker
on package `9e278fd21933f3c0`. All initial Cortex writes succeeded. Documenting a
manual native-profile registration step did not fix marketplace-only installation.

The current package is `1.15.6+codex.sha256.ee8d25d519f1b22f`, full payload SHA-256
`ee8d25d519f1b22fdab9aa6b172e5f1916b9b96f7499706ab21fae27ae55839a`.
All 22 complete specialist profiles are now ordinary packaged worker skills,
generated from the same shared reporting protocol and specialization sources.
The coordinator assigns an exact skill token to an ordinary native subagent.
No custom profile selector, personal agent registration, or lifecycle hook is
required. Optional TOML exports remain separate. Dev preparation performs only
marketplace installation and never populates the personal agents directory.

Consecutive real CLI and Desktop runs on this unchanged payload started with zero
personal agent TOMLs. The ordinary workload added an English offline-use sentence
to a fixture README. CLI ran two workers, including independent verification that
read its predecessor's report through Cortex. It produced 16 MCP events and 100
host-call records, then completed with an explicit exit-zero marker. Desktop's
composer was visually confirmed, one native task receipt followed submission,
and its native task-complete event was observed. Desktop produced 13 MCP events
and 55 host-call records with one worker. Both complete audits found no Cortex
errors, hidden command failures, truncation, policy violations, open command
sessions, or open execution cells. Both exact test sessions were stopped.

The CLI store contains one task, five report documents, one canonical pipeline,
and six editions. Desktop contains one task, four report documents, one pipeline,
and five editions. Both passed SQLite integrity/foreign-key checks, Markdown
size/hash/permission checks, and absence of unpublished drafts or pending deletes.
The observer distinguishes exact skill instruction reads from project work and
recognizes explicit standalone command-exit receipts. It does not decrypt native
assignments or call skill loading native TOML attachment. Project development
quality remains separate from Cortex acceptance.

The full source suite passed 92 tests; all 18 focused package/observer tests also
passed after the final classification adjustment. Package validation, source-only
sync, links and diff checks passed. All 22 worker skills and the coordinator pass
skill validation. The preliminary
CLI run on `4889663ba29bbfa8` established worker execution but is not the final
qualification: its instruction-read wrappers omitted exit receipts. Final runs
above exposed command receipts. Private full calls, events and screenshots remain
outside the repository; only bounded aggregate evidence is documented here.

The older sections below describe earlier candidates and do not establish current
marketplace readiness.

# Ordinary-install regression — 2026-09-05

An ordinary Desktop task on `e3cbb23ec9b5e373` saved its initial pipeline but
stopped before delegation because native specialist selection was unavailable.
The ordinary Codex home had zero native profile files; the dev home had 22.
The earlier real-host checks established operation in the prepared dev environment,
not completeness of marketplace-only installation. That distinction was missing
from the earlier readiness conclusion.

Candidate `1.15.6+codex.sha256.9e278fd21933f3c0` adds the explicit packaged
`cortex_setup.py` registration/check operation. The dev preparer now calls this
same installer instead of maintaining a separate copy path. The recommended
installation prompt includes registration and native-spawn verification; missing
profiles no longer lead to a misleading suggestion to switch to `normal`.

All 87 source tests pass, including empty native registry detection, explicit
registration, exact-byte verification, conflict refusal before any profile writes,
managed refresh, unrelated-file preservation and symlink rejection. Stable setup
was checked read-only: 22 missing, no conflicts. Registration is a distinct user
operation and is not performed by MCP initialization or a workflow hook.
CLI and actual Desktop passed consecutive Cortex audits on this unchanged
candidate using the common installer. CLI: 15 MCP events including initialization,
80 host-call records, two native specialists, no Cortex errors or coordination
violations, and exit zero. Desktop: 13 MCP events including initialization,
46 host-call records, one native specialist, and no Cortex errors or coordination
violations; its task completed and the disposable process was stopped. Both hosts
attached the selected profiles and published worker reports successfully. These
runs verify the setup route; they do not claim that marketplace installation by
itself installs native profiles or that the ordinary user registry was modified.

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
