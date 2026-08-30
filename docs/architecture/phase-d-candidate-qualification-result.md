# Phase D exact-candidate qualification result

Date: 2026-08-29  
Base version: `1.12.1`  
Status: **blocked; no candidate pass claimed**

## Evidence

The isolated candidate was refreshed only under
`/home/igovet/.cortex-dev/.codex` using the supported isolated sync path. The
machine-readable receipt verified `base_version=1.12.1`, `parityVerified=true`,
and matching source/candidate digest
`sha256:39bfb3402ab88178c5aeab22f66d97bddc68927f06e294f714c18f4e3e672999`.
The receipt identified the content-addressed suffix
`sha256.39bfb3402ab88178`. No stable profile, stable plugin, tmux, or live-dev
session was touched. Candidate subprocesses removed `PYTHONPATH` and
`CORTEX_SOURCE_MODE` and used `PYTHONDONTWRITEBYTECODE=1` with `python3 -B`.

Receipt verification command:

```text
PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/cortex_candidate_receipt.py verify --source-root /home/igovet/Web_Projects/codex-orchestration --owner-home /home/igovet --isolated-home /home/igovet/.cortex-dev --isolated-codex-home /home/igovet/.cortex-dev/.codex
```

## Qualification commands and outcomes

The requested installed-candidate run was attempted first:

```text
PYTHONDONTWRITEBYTECODE=1 CORTEX_PHASE_D_CANDIDATE_ROOT=/home/igovet/.cortex-dev/.codex/plugins/cache/cortex/cortex/1.12.1+codex.sha256.39bfb3402ab88178 python3 -B -m pytest -q tests/test_phase_d_decision_qualification.py
```

Result: **11 errors during fixture setup, 0 tests executed**. The strict
qualification manifest attempted to compute the full source manifest against
the installed plugin-only cache and rejected the absent
`.agents/plugins/marketplace.json`. This is a harness/provenance integration
defect, not evidence of runtime success or failure. It must be corrected by
the owning qualification harness without weakening extra-file or provenance
checks.

For separation of concerns, the same suite was then run against its supported
fresh content-addressed staging path:

```text
PYTHONDONTWRITEBYTECODE=1 python3 -B -m pytest -q tests/test_phase_d_decision_qualification.py
```

Result: **11 passed, 0 failed, 0 skipped, 0 collection errors**. This proves
the current suite passes through a real staged stdio MCP handler, but it is
not a pass of the installed isolated cache because the required installed
candidate fixture path failed before dispatch.

## Gate disposition

The complete requested catalogue/migration/event-journal/wire-size/path and
80-pair gate is **not qualified**. No row is promoted based on the fresh
staging result. The next owner action is to make the fixture consume the
verified isolated candidate while comparing only the candidate's declared
manifest scope to the source manifest, preserving strict rejection of missing,
extra, symlinked, or altered files; then rerun the entire suite with zero
setup errors and zero skips. Live-dev remains unrun by design.

## Harness/provenance correction — pending installed rerun

The fixture now resolves an installed override only through the verified
isolated receipt and compares it using the explicit installable-plugin manifest
scope. Repository delivery files are still validated by source candidate and
marketplace gates, but are no longer incorrectly required under the native
plugin-only cache root. Focused source regressions prove that a cache without
the repository marketplace file is valid only when every declared plugin file
is present and byte-identical; missing, extra, symlinked, or modified plugin
payload still fails closed. This is a harness correction, not installed
candidate evidence. The full installed-cache command above must be rerun before
this document or any Phase D row can claim a candidate pass.

## Fresh installed-cache rerun — 2026-08-29

The isolated candidate was refreshed again through the supported sync path and
the receipt remained authoritative: base `1.12.1`, content-addressed suffix
`sha256.39bfb3402ab88178`, `parityVerified=true`, and matching
source/candidate digest `sha256:39bfb3402ab88178c5aeab22f66d97bddc68927f06e294f714c18f4e3e672999`.

```text
PYTHONDONTWRITEBYTECODE=1 CORTEX_PHASE_D_CANDIDATE_ROOT=/home/igovet/.cortex-dev/.codex/plugins/cache/cortex/cortex/1.12.1+codex.sha256.39bfb3402ab88178 CORTEX_PHASE_D_ISOLATED_OWNER_HOME=/home/igovet python3 -B -m pytest -q tests/test_phase_d_decision_qualification.py
```

Result: **8 passed, 3 failed, 0 skipped, 0 collection errors**. The failures
are qualification-harness path assumptions:

1. Provenance expects `candidatePath` under `<installed-root>/plugins/cortex`,
   although the installed cache path itself is already the plugin root and the
   server correctly reports that exact root.
2. The concurrent-open test constructs
   `<installed-root>/plugins/cortex/scripts/cortex.py`, which does not exist.
3. The 80-pair stress setup uses the same invalid nested server path and cannot
   begin.

The eight dispatched cases passed through the installed candidate's real stdio
handler. The gate remains blocked because the full suite did not execute; no
stress or candidate-qualified status is claimed. The fixture must normalize the
installed root once, use the receipt's exact `candidate_path`, and derive every
child server path from that root before rerunning.

## Canonical candidate-location correction — pending installed rerun

Qualification now has one typed candidate-location resolver. It distinguishes a
checkout/complete-release root from a native installed-plugin root and returns
the validated plugin root, server path, and runtime-package path exactly once.
The installed constructor accepts only the root named by the already verified
receipt; it never appends `plugins/cortex`, scans for another candidate, or
falls back to source/staging. The release constructor requires the complete
release topology and cannot reinterpret a plugin root as a release root.

All Phase D branches—including provenance, concurrent open/record, and the
80-pair stress setup—now consume that one resolver result. Focused source tests
cover checkout, staged release, exact installed root, wrong root, nested
duplicate plugin root, and existence of the server before launch. This removes
the path-model defect only. The full receipt-selected installed-cache command
still must be rerun; no candidate or live status is promoted here.

## Canonical CandidateLocation rerun — 2026-08-29

The isolated candidate was refreshed through the supported path. Receipt
verification passed for base `1.12.1`, candidate
`1.12.1+codex.sha256.39bfb3402ab88178`, `parityVerified=true`, and matching
source/candidate digest
`sha256:39bfb3402ab88178c5aeab22f66d97bddc68927f06e294f714c18f4e3e672999`.
The fixture consumed the receipt-selected `CandidateLocation`; no checkout
`PYTHONPATH`, source mode, stable profile, tmux, or live-dev session was used.

```text
PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/cortex_candidate_receipt.py verify --source-root /home/igovet/Web_Projects/codex-orchestration --owner-home /home/igovet --isolated-home /home/igovet/.cortex-dev --isolated-codex-home /home/igovet/.cortex-dev/.codex
PYTHONDONTWRITEBYTECODE=1 CORTEX_PHASE_D_CANDIDATE_ROOT=/home/igovet/.cortex-dev/.codex/plugins/cache/cortex/cortex/1.12.1+codex.sha256.39bfb3402ab88178 CORTEX_PHASE_D_ISOLATED_OWNER_HOME=/home/igovet python3 -B -m pytest -q tests/test_phase_d_decision_qualification.py
```

Result: **11 passed, 0 failed, 0 skipped, 0 collection errors** in 20.34s.
All six public decision operations, replay/conflict/stale/cross-project
handling, restart/reconciliation, plan outcomes and immutable relation,
steering supersession, advisory ownership, provenance, and the 80-pair
concurrency stress passed through the real installed-candidate stdio handler.
The installed-cache candidate gate is qualified; the separate LLM-driven
live-dev gate remains unrun.

## v19 task-locator and filesystem-policy candidate gate — 2026-08-29

The isolated `1.12.1` candidate was freshly refreshed after v19 source
clearance. Receipt/tree verification passed with `parityVerified=true`, exact
installed path under `/home/igovet/.cortex-dev/.codex`, and matching
source/candidate digest
`sha256:09915eac1b92a81d53d501c4ee5cab0e7222a4bda3f28b8bc5fc93b2cee18628`.
Candidate subprocesses removed checkout `PYTHONPATH` and source mode; the
runner's minimal import path was used only for test harness helpers.

```text
PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/cortex_candidate_receipt.py verify --source-root /home/igovet/Web_Projects/codex-orchestration --owner-home /home/igovet --isolated-home /home/igovet/.cortex-dev --isolated-codex-home /home/igovet/.cortex-dev/.codex
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/home/igovet/Web_Projects/codex-orchestration/plugins/cortex/scripts CORTEX_PHASE_D_CANDIDATE_ROOT=/home/igovet/.cortex-dev/.codex/plugins/cache/cortex/cortex/1.12.1+codex.sha256.09915eac1b92a81d CORTEX_PHASE_D_ISOLATED_OWNER_HOME=/home/igovet python3 -B -m pytest -q tests/test_phase_d_decision_qualification.py
11 passed in 22.67s
```

The run covered the v18/v19 semantic MCP paths, exact intent→lease→claim→
`server_ready` observation, task-locator/filesystem-policy boundaries,
replay/conflict/stale/restart/cross-task behavior, automatic migration on
existing/new stores, and the 80-shard/160-process first-call stress. No live,
tmux, or stable-profile mutation was performed.

## Exact-session lease topology correction — 2026-08-29

Source/staged fixtures now provision no observation lease and treat
`observation=limited` as an explicit nonblocking condition. Lease and
`server_ready` assertions are restricted to receipt-selected installed-cache
targets, using the signed generation under `.cortex-mcp-observations`.
This preserves the strict `candidate_codex_home` boundary and prevents source
or staged release roots from claiming installed-session observation.

Source-focused command result:

```text
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/home/igovet/Web_Projects/codex-orchestration/plugins/cortex/scripts python3 -B -m pytest -q tests/test_phase_d_decision_qualification.py tests/test_mcp_event_journal.py tests/test_candidate_provenance.py tests/test_clarification_holds.py tests/test_worker_handoff_contract.py
65 passed, 5 subtests passed in 25.98s
```

Prompt lint and marketplace validation both passed after removing the exact
generated bytecode artifacts. The installed-candidate lease/server-ready gate
must be rerun separately after this harness-only topology correction; no
candidate refresh or live-dev run was performed in this task.

## Server-ready invariant rerun — 2026-08-29

After the server-ready and first-call route-invariant changes, the isolated
candidate was refreshed with base `1.12.1` and content-addressed digest
`sha256:41fcbba0795161f27ee2738ce63dd1afca5af33ec066971d5a9a3f2b22f49fc6`.
Receipt verification reported `parityVerified=true` and the exact installed
candidate path; subprocesses removed checkout `PYTHONPATH` and source mode.

The complete receipt-selected installed-cache command passed:

```text
PYTHONDONTWRITEBYTECODE=1 CORTEX_PHASE_D_CANDIDATE_ROOT=/home/igovet/.cortex-dev/.codex/plugins/cache/cortex/cortex/1.12.1+codex.sha256.41fcbba0795161f2 CORTEX_PHASE_D_ISOLATED_OWNER_HOME=/home/igovet python3 -B -m pytest -q tests/test_phase_d_decision_qualification.py
11 passed, 0 failed, 0 skipped, 0 collection errors in 20.28s
```

The black-box initialization assertion observed exactly one sanitized
`server_ready` event with the receipt build identity, exact 15-tool catalogue
count, and matching catalogue digest, with no workload anchors. Source prompt
lint also passed (`contract-lint: passed`), and the installed bundled skills
were verified present. No tmux/live-dev run was performed.

## Runtime receipt phase-separation gate — 2026-08-29

Fresh isolated refresh produced base `1.12.1` candidate
`1.12.1+codex.sha256.09915eac1b92a81d`. Receipt verification passed the strict
`parity_verified=true` field and matching source/candidate digest
`sha256:09915eac1b92a81d53d501c4ee5cab0e7222a4bda3f28b8bc5fc93b2cee18628`.

The receipt-selected installed-cache Phase D suite passed `11 passed` in
`22.76s`, including exact lease/server-ready events and 80-shard/160-process
stress. Supporting provenance, exact-session lease, and event-journal tamper
suites passed `53 tests` in `10.86s`. No source mutation, pycache state,
candidate tamper, or receipt tamper was accepted; no live/tmux or stable-profile
mutation was performed.

## No-bytecode launch and lifetime gate — 2026-08-29

The packaged `.mcp.json` is validated as the direct `python3 -B` server
launcher; the marketplace validator now enforces that exact no-bytecode
contract. Source workflow, provenance, exact-session lease, and event-journal
tamper checks passed `60 tests` in `40.36s` after the validator correction.

A fresh isolated `1.12.1` candidate was then staged and receipt-verified with
`parity_verified=true`, candidate suffix
`sha256.1c39e8793abf3350`, and matching source/candidate digest
`sha256:1c39e8793abf33504abf7c69dda88ebabe7d5794da6c7058c7509e45e398063e`.
The complete installed Phase D suite passed:

```text
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/home/igovet/Web_Projects/codex-orchestration/plugins/cortex/scripts CORTEX_PHASE_D_CANDIDATE_ROOT=/home/igovet/.cortex-dev/.codex/plugins/cache/cortex/cortex/1.12.1+codex.sha256.1c39e8793abf3350 CORTEX_PHASE_D_ISOLATED_OWNER_HOME=/home/igovet python3 -B -m pytest -q tests/test_phase_d_decision_qualification.py
11 passed in 22.74s
```

The run included exact lease/server-ready events, repeated semantic calls,
80-shard stress, and candidate/receipt tamper fail-closed coverage. No pycache
or `.pyc` artifacts remain. No live/tmux or stable-profile mutation was
performed.
