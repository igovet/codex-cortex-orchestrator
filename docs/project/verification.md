# Verification index

<!-- GENERATED:START -->

Cortex verification distinguishes source tests, real source MCP transport,
native CLI and actual Desktop. A command is evidence only when it was run.
Current completion and unrun gates live in the
[Completion checklist](typed-orchestration-integrity.md#11-completion-checklist).

## Freeze and refresh

Keep the semantic version at 1.15.6 for this hardening task. Any installable
payload edit invalidates the previous cache stamp and subsequent release-sensitive
results. Refresh through the supported isolated entry point before validators:

```bash
./scripts/cortex-dev --prepare-only
```

Use its exact printed content-addressed version for the source manifest. Never
invent a suffix or invoke cachebuster/install helpers against the stable profile.
The isolated candidate is under HOME/.cortex-dev, not the user's stable plugin.

Run release-sensitive checks sequentially in this checkout. Sync tests can
create bounded source fixtures that concurrent validators would mistake for
payload drift.

## Source and package commands

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/cortex-prompt-lint.py
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=plugins/cortex/scripts python3 -B -m pytest -q
PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/validate-cortex-marketplace.py
PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/verify-cortex-release.py --mode source
git diff --check
./scripts/sync-cortex.sh --dry-run
```

Syntax validation uses in-memory parsing/compilation; do not create package
bytecode as a substitute. Source checks validate the complete allowlisted
payload, manifest, runtime import closure, static catalogue, hooks and profiles.
They do not prove that a native model performed a correct first call.

Before an actual release, the separately relevant committed-payload gate is:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/verify-cortex-release.py --mode head
```

A dirty source checkout can pass source validation without satisfying that
committed-release gate. Do not commit, install or publish merely to change a
verification label.

## Qualification ladder

Follow the complete [levels A–G](typed-orchestration-integrity.md#10-qualification-ladder)
in order. A defect returns to its smallest affected local test, not immediately
to the full live matrix.

| Level | Evidence required |
| --- | --- |
| A | All twenty operations with legal prerequisites and deliberate negative cases; no no-op steering |
| B | Every packaged profile consumes its assignment and uses its fixed terminal kind correctly |
| C | Typed DAG, acceptable predecessor evidence, artifact generations, races and finite remediation |
| D | Multiple in-flight messages, real steering at different stages, quiescence and same-task recovery |
| E | Short real CLI product task through verified result, user closure review and clean completion |
| F | One complete all-tools/all-profiles CLI scenario on the frozen payload |
| G | Complete actual Desktop repetition on the same unchanged payload after CLI |

The source/API full matrix is not native evidence. All 22 profiles must receive
meaningful scope that is ready when assigned. Conditional tools require real
point replacement, a genuine branch, recovery or requested chronology; calling
them merely for coverage violates the protocol.

## API and first-call acceptance

Verify the authoritative static registry contains exactly twenty operations.
The advertised closed input schemas and runtime validation share one source.
Tool descriptions derive required-property lists from those schemas. Keep the
complete catalogue within its discovery bound and reserve; never truncate tools
or weaken required fields to fit.

Check malformed types, missing/extra properties, Unicode and aggregate size
limits, invalid selectors and conditional metadata. Rejection must be atomic
and diagnostics value-blind. Missing risk/unresolved evidence must not silently
become empty arrays. Corrective calls do not qualify as clean native first calls.

Publications enforce the node's declared kind, complete exact node/check
coverage and one cross-kind terminal slot. Plans contain expected checks;
result/documentation coverage contains observed facts. No legacy envelope,
progress publication, chunk-assembly continuation or alternate scope is accepted.

Worker evidence must expose one immutable assignment authority. Contextual
outcome verification prose cannot substitute for node-local selectors or add
downstream work to a baseline assignment.

Useful focused suites include:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=plugins/cortex/scripts python3 -B -m pytest -q tests/test_public_mcp_first_call_conformance.py tests/test_public_schema_separation.py tests/test_bootstrap_readiness.py tests/test_retired_worker_routes.py
```

## Identity, hooks and native correlation

Test both initial neutral discovery and committed worker audience. A retained
initial catalogue must not bypass actor checks. A foreign pending assignment
cannot select a new connection's audience. No mid-turn refresh is required for
worker publication.

For every new non-replayed assignment, observe one immediate exact native spawn,
the actual child correlation and its first assignment read. A copied worker
reference, coordinator pivot, sibling read or mismatched dispatch must fail
without publication.

A timeout or isolated lifecycle event is not completion or loss. Verify complete
native evidence plus absent report before loss recovery. Resume must retain the
existing task and read current state, then any required active continuations;
timeline cannot substitute for recovery.

## DAG, artifact and revision acceptance

Initial baseline must complete before artifact-dependent discovery/planning.
Candidate structural validation precedes independent semantic validation;
delivery stays blocked for an incomplete or invalid candidate.

Race claims for the same node and for conflicting mutation ownership. Compatible
read-only audits can run in parallel on a sealed generation. Implementation-
dependent audits must never start against unfinished implementation.

Verify matching read-only observations, mutator successor generations,
external-change snapshot conflicts, reconciliation barriers and historical
old-generation evidence. Unavailable manifests cannot become a matching
fingerprint. Run the observation procedure under the real workspace sandbox.

Steering must atomically record a genuine semantic delta and return every
affected protected native task. Observe interruption and quiescence before a
successor baseline. A racing superseded publication creates no report or slot.
Preserve stopped-worker writes as evidence rather than silently discarding them.

Exercise validated in-contract remediation, independent regression, progressive
replanning/strategy selection and finite budget exhaustion. Scope, authority
and material risk findings cannot masquerade as ordinary repair.

## Decisions and documentation

Low-risk complete plans continue without questions unless review was requested.
Material risk, external authority or a genuine product choice requires the
current complete plan packet. One validated-alternative choice selects its
delta and graph atomically; unselected branches remain unauthorized.

Approval fulfills its decision boundary. Authorized replanning must not ask
again merely because a plan changed. A renewed user request or materially new
risk/authority establishes a fresh boundary.

Documentation edits precede final verification. Final checks and read-only
documentation-impact evidence bind the same latest generation. A no-impact
rationale must be worker-owned and must not cause a pointless edit.

Always present the verified result and links before the fresh closure review.
Only the direct current close choice permits closure. A revise answer keeps
the task open. Backend evidence and generation gates remain mandatory.

## Human views and storage

Verify current-only schema v2, exact governance/timeline shape, project isolation,
private ownership/modes and no migration of older databases/directories.
The removed initiative and alternate publication/service routes must be absent.

Plans and finalized reports are derived readable Markdown, not raw JSON.
Only a contained regular file with current source and digest/readback parity
may produce a link. Unknown report formats fail; they do not use a generic
legacy fallback. External edits must be preserved as conflicts.

Force a post-commit view failure for each terminal kind. One transient I/O
failure may repair the original request without another report; persistent
failure stays explicit. Unsafe paths, permission denial and external edits are
not bypassed. Test before-write and after-write cases, exact report/slot counts
and bounded attempts. Repeat the required projection-repair boundary in real
CLI qualification; local fault injection alone is not that live gate.

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=plugins/cortex/scripts python3 -B -m pytest -q tests/test_publication_projection_repair.py tests/test_current_storage_schema.py tests/test_artifact_worker_sandbox.py
```

## Actual CLI live-dev

Use only the supported ordinary interactive session in a separate canonical
test project:

```bash
./scripts/cortex-live-smoke start --workdir /absolute/test/project
./scripts/cortex-live-smoke status
./scripts/cortex-live-smoke capture
./scripts/cortex-live-smoke events
TERM=xterm-256color tmux -f /dev/null attach -t cortex-v12-smoke
```

The launcher refreshes the isolated candidate through scripts/cortex-dev.
Before workload input, observe the actual trust/composer state and passive
host-owned receipt proving candidate, registered server and catalogue identity.
If the fresh-project trust screen visibly requests acknowledgement, use the
separate enter action exactly once, then observe the composer:

```bash
./scripts/cortex-live-smoke enter
./scripts/cortex-live-smoke send --prompt-file TASK_PROMPT.txt
```

Do not use enter as a blind retry. The prompt must begin with the real
$cortex:orchestrator token; it is the prompt's only Cortex-specific content.
Everything after it is an ordinary product request, without protocol coaching,
internal tool rules or success sentinels.

The transport normalizes one line, sends the complete prompt literally once,
waits five real seconds and sends one named Enter. It never splits long
messages, sends compensating keys, decides readiness or approves a question.
The delivery receipt is not TUI acceptance. Inspect the attached terminal when
bounded capture is stale.

The external LLM operator observes the model's decision packet and sends the
appropriate ordinary reply for the predefined scenario. During active work,
send the specified multiple messages and genuine changes, then verify order
and resulting revisions. The operator, not a transport parser, judges success.

For same-process-thread recovery, stop only the exact session and restart with
the same project:

```bash
./scripts/cortex-live-smoke start --workdir /absolute/test/project --resume-last
```

Confirm the old transcript/task before continuation. A new task opening fails
resume qualification.

Inspect every worker's bounded sanitized events, including the first terminal
call. Any first-call tool/validation error, premature assignment, unexplained
replay or missing expected completion fails. A corrected publication is not a
pass. Superseded/snapshot-conflict states count only as their declared clean
non-publication outcomes, never as successful reports.

Capture explicit clean process completion before cleanup:

```bash
./scripts/cortex-live-smoke capture
./scripts/cortex-live-smoke stop
```

After failure, use the exact-session interrupt cleanup. Never kill the tmux
server, use an alternate socket, run codex exec or modify stable HOME/CODEX_HOME.

## Full CLI and real Desktop

Only after local levels and the short CLI pass, run the complete ordinary
scenario from tests/fixtures/live_cortex_all_tools_scenario.json. Verify all
twenty tools, all 22 meaningful profiles, ready-node parallelism, message load,
several semantic steering revisions, recovery and mandatory closure.

Then repeat fully in the real disposable Desktop host, on the same payload:

```bash
./scripts/cortex-desktop-dev start --workdir /absolute/test/project --prompt-file TASK_PROMPT.txt --model gpt-5.6-luna --reasoning-effort high
./scripts/cortex-desktop-dev status
./scripts/cortex-desktop-dev stop
```

Use the supported Desktop observation and delivery surfaces for the complete
multi-turn scenario. Merely launching Desktop or observing a partial flow is
not parity. Any payload edit invalidates both earlier host results.

## Maintenance and privacy

Maintenance is a separately invoked non-MCP administrator CLI. Test read-only
health, whole-shard backup, exact confirmations, offline restore, projection
pruning and backup retention without canonical data loss or arbitrary paths.
See [operator maintenance](../features/operator-maintenance/index.md).

Never expose raw private logs, worker reports, credentials or task exports.
Record only sanitized operation/outcome, exact candidate/host, decisive check,
failure or unrun gate and cleanup outcome.

## Documentation review

Re-read README, SECURITY and affected Markdown against source, tests, schemas,
skills and executable configuration. Check links/anchors, diagrams, commands,
schema/release identity, model omission/effort rules and actual tool count.
Documentation cannot reintroduce removed initiative routes, legacy migrations,
textual assignments or intermediate publications.

Update the Completion checklist after each verified change. Do not mark a
parent invariant complete based only on one source test or a partial live run.

<!-- GENERATED:END -->
