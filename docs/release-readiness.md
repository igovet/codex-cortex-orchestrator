# Release readiness

Status: **not qualified for release**. The implementation and qualification
record is the [typed integrity specification](project/typed-orchestration-integrity.md),
especially its Completion checklist. Passing source tests does not change the
CLI or Desktop gates to passed.

## Candidate identity and isolation

- Semantic version remains **1.15.6**, as requested; only the content-addressed
  `+codex.sha256.<digest-prefix>` cache identity changes.
- SQLite uses current schema **2**, created directly. Old/unknown formats are
  rejected without migration, adoption, fallback, or compatibility writes.
- The installable contract lives below `plugins/cortex/`; repository tests and
  documentation are development support, not runtime authority.
- All development installation uses `./scripts/cortex-dev` and its exact
  isolated `$HOME/.cortex-dev` candidate. Stable Codex configuration, plugin,
  credentials and task data are not synchronized or replaced.
- Every payload change requires a fresh candidate stamp before release-sensitive
  checks. It invalidates both earlier CLI and Desktop results.

The complete current catalogue contains twenty tools:

`open_task`, `read_task`, `read_state`, `read_scope`, `read_outcome`,
`read_continuations`, `read_evidence`, `read_timeline`,
`open_clarification`, `record_clarification`, `open_plan_review`,
`record_plan_review`, `open_steering`, `record_steering`,
`open_assignment`, `publish_plan`, `publish_result`,
`publish_documentation`, `assess_governance`, `close_task`.

The live advertised schemas are the only call-shape authority. Complete
catalogue discovery must fit below 65,536 bytes with a 4,096-byte reserve.
Runtime validation and actor isolation remain authoritative even when a host
retains an earlier catalogue projection.

## Integrity gates

The LLM chooses intent, decomposition, profiles, effort, ready-node dispatch,
interpretation and remediation. The backend stores the typed graph and
deterministically checks admissibility; it does not select or spawn workers.

Source and wire tests must establish:

- Task creation and assessment precede dispatch. Baseline evidence precedes
  artifact-dependent discovery and planning. Scope reads expose actual
  bootstrap readiness and reasons, not merely graph existence.
- Dependencies and capabilities are structural. Implementation-dependent
  audits wait for acceptable terminal predecessors on the correct generation.
  Only ready compatible work runs in parallel. Mutation isolation does not
  impose a universal one-worker limit.
- Native dispatch is correlated to its exact assignment and consumed first
  by its host-bound child. One typed assignment is worker authority for nodes,
  checks, artifact procedure and terminal kind; scoped contract context
  preserves product requirements without duplicating assignment instructions.
- Node purpose fixes plan/result/documentation publication. Each assignment has
  one cross-kind terminal slot. Observed coverage is canonical; no duplicate
  caller-authored verification-facts array exists.
- Every result explicitly supplies artifact observations, or null only for
  artifact-independent work. Missing observations never imply independence.
- Atomic transitions include state, chronology and command receipts.
  Identical ambiguous reconciliation preserves identity; changed payloads
  conflict. An observed successful mutation is not permission to replay it.
- Steering atomically revises the contract and invalidates affected authority.
  Superseded publication cannot mutate current evidence. Quiescence and
  reconciliation precede work on the changed artifact boundary.
- Recovery uses current state then active continuations when unfinished work
  exists. A copied reference or fresh connection cannot adopt worker authority.
  Silence or timeout is not proof of worker loss.
  Ordinary claim versus loss reconciliation is server-derived from selected
  ownership, not a second caller mode. State, continuation and scope reads must
  agree on fresh signed quiescence without mutating ownership while reading.
- Finite in-contract repair and independent verification need no manufactured
  user authorization. Non-progress and exhausted budgets cannot certify success.
- Minimal/light risk-free plans continue informationally unless explicit review
  or a genuine decision is required. High-risk/material plans require the exact
  current verified plan packet and approval binding. Routine fixes do not
  manufacture a semantic revision.
- Derived Markdown is verified against canonical evidence before a link is
  returned. Post-commit projection failure never duplicates the report.
  Missing, stale, unsafe or mismatched views are not valid review links.
- Closure follows presentation of the current result, mandatory final closure
  review, and an explicit current close choice. Earlier plan approval, silence,
  or a request to close automatically is insufficient. Incomplete requirements
  cannot be closed even with that choice; risk wording cannot waive checks.

The [ledger page](features/orchestration-ledger/index.md),
[human views](features/human-readable-task-views/index.md) and
[security policy](../SECURITY.md) describe the current boundaries. Remaining
obsolete internal routes must be removed, not described as supported legacy.

## Model routing

| Route | Effort | Use |
| --- | --- | --- |
| Configured Luna default | low through max | Most work, including demanding bounded tasks |
| Explicit Terra | low through max | Genuinely complex architecture or planning |
| Explicit Sol | low through max | Rare, very-high-risk security work |

Native Luna dispatch omits the model override. Each dispatch carries the
chosen effort; `ultra` is never allowed. Profiles do not automatically select
a model, and failures do not authorize an automatic escalation ladder.
All 22 packaged profiles require source and real-host evidence.

## Qualification order

Freeze the candidate, then qualify each level. A defect returns to its narrow
regression before any large rerun:

1. Contract/admission tests for every tool, including valid prerequisites,
   invalid calls and atomic non-mutation.
2. Every profile consumes its assignment and publishes its assigned kind.
3. DAG, artifact, parallel ownership, steering load and recovery tests.
4. A short real interactive CLI end-to-end scenario.
5. One full all-tools/all-profiles CLI scenario with several genuine steering
   changes, concurrent messages, point replacement, chronology and resume.
6. The full actual Desktop scenario on the same stamped payload.

Source/API matrices do not prove transport, hooks, native model behavior,
message delivery, resume or Desktop parity. Conditional operations must have
real prerequisites; no no-op steering or tool calls merely for coverage.

Happy-path qualification requires zero first-call tool/validation errors,
unexplained mutation replays, premature assignments, corrected publications,
missing tools or missing profiles. The revised full scenario separately declares
fault injections and proves bounded recovery without weakening obligations;
expected faults are not mislabeled as clean first-call success. Unexplained
errors or unverified recovery fail. A `partial` caused by starting an audit
before its implementation exists fails the run. The external operator inspects
every native worker event stream, not only the coordinator pane.

## Commands and live operation

Run source checks sequentially in one checkout:

```bash
./scripts/cortex-dev --prepare-only
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=plugins/cortex/scripts python3 -B -m pytest -q
PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/cortex-prompt-lint.py
PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/validate-cortex-marketplace.py
PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/verify-cortex-release.py --mode source
git diff --check
./scripts/sync-cortex.sh --check
```

Prepare refreshes the isolated candidate. Ensure the source manifest carries
the matching cache stamp before the later release-sensitive checks.
A committed-candidate check is separate and cannot certify uncommitted changes.

For the ordinary interactive CLI:

```bash
./scripts/cortex-live-smoke start --workdir /absolute/test-project
./scripts/cortex-live-smoke status
./scripts/cortex-live-smoke capture
./scripts/cortex-live-smoke events
# Only after visibly observing the fresh-project trust screen:
./scripts/cortex-live-smoke enter
# Then visibly confirm the interactive composer and activation receipt:
./scripts/cortex-live-smoke send --prompt-file /absolute/task-prompt.txt
./scripts/cortex-live-smoke capture
./scripts/cortex-live-smoke events
./scripts/cortex-live-smoke stop
```

Use the default tmux server and exact `cortex-v12-smoke` session, never
`codex exec`, nested tmux or an alternate socket. The helper exposes bounded
observations and transport only; it does not decide readiness or acceptance.
Early text or submission can be lost during TUI initialization.
A passive host-owned activation receipt must match the exact isolated candidate,
registered server and advertised catalogue identity before workload submission.

The real `$cortex:orchestrator` token is the only permitted Cortex-specific
content in the workload. Everything following it is an ordinary user request.
The transport contract is one literal normalized insertion, a five-second wait
and one standalone named Enter. Receipt proves delivery, not TUI acceptance.

Use a multi-turn separate test project. Observe the visibly rendered plan,
approve only its actual current packet when required, and exercise
planner → implementation → independent verification → documentation-impact assessment → closure.
The operator supplies authorized disposable-test branch or API-key/ENV answers,
steering and message bursts without teaching tool parameters. Inspect the
bounded sanitized structured event stream for each child. Any undeclared hidden
tool error, unexplained replay, traceback or `schema_unsupported` fails the gate.
Declared recovery faults require their own complete evidence and cannot excuse
an unrelated failure or a changed contract.

For process recovery stop only the exact session, restart with the same
`--workdir` and `--resume-last`, verify the same transcript/task, and then
continue. A new task creation is a failed resume check.
Successful cleanup requires observing `Cortex live-dev exit=0`; failure uses
`stop --interrupt` and remains a failure.

Real Desktop uses `./scripts/cortex-desktop-dev`, the same isolated candidate,
the actual Desktop binary and a disposable Electron profile. CLI and Desktop
must finish consecutively on the same cache-stamped payload.

## Evidence and handoff

Update the specification's Completion checklist after every verified step.
Record exact commands, candidate suffix, tested surface, observed result,
cleanup and unrun gates. Never copy private logs, worker reports or secrets
into repository evidence. Keep source, candidate, real CLI and real Desktop
evidence separate.

Before release re-read README, SECURITY and affected Markdown. Check links,
commands, diagrams, versions, routing and protocol claims against source.
[Operator maintenance](features/operator-maintenance/index.md) remains a
separate explicitly invoked host-private CLI; restore is offline and old
storage is not adopted. No release-ready claim is valid while required
qualification remains unrun or fails.
