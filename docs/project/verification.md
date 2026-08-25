# Verification index

<!-- GENERATED:START -->

This page describes source-mode checks for Cortex 11.0.1 and the v11 task and
capability contract on SQLite schema v17. A command is evidence only
when it was actually run; installation and live-model checks must be recorded
separately.

## Fast source checks

~~~bash
python3 -m compileall -q plugins/cortex/scripts
python3 scripts/validate-cortex-marketplace.py
python3 scripts/cortex-prompt-lint.py
python3 scripts/cortex-prompt-eval.py
git diff --check
~~~

Run focused tests for changed areas, then the complete source suite:

~~~bash
python3 -m pytest -q tests/test_attempt_protocol.py tests/test_attempt_facade_lifecycle.py
python3 -m pytest -q
~~~

The repository uses pytest discovery for the unittest-style suite. Record total,
passed, skipped, and failed counts, plus the exact command and environment.

## Protocol acceptance

Focused protocol checks must cover:

- AttemptEvent append-only ordering, stable event keys, bounds, and retries;
- AttemptResult semantic payload validation and server-owned metadata;
- RUNNING → WORK_COMPLETED → FINALIZING → COMPLETED;
- explicit BLOCKED and FAILED outcomes;
- same-attempt finalization after view, serialization, or infrastructure failure;
- server-derived changed files, checks, timestamps, identity, and observations;
- exact task/attempt/result scope for briefing and predecessor-result reads;
- exact coordinator `task_ref`/`coordinator_ref` and worker
  `task_ref`/`assignment_ref` capability scope;
- native `spawn_agent`/exact `wait`/read/continue ordering, with no
  session/environment authorization or manually authored advance/completions;
- digest- and capsule-bound plan and outcome repair through `complete_attempt`,
  including self-contained multi-error patch cards, retryable malformed-copy
  recovery, explicit allowed patch operations, and terminal correctly-shaped
  MAC tampering;
- malformed calls for every public tool branch, each with an exact JSON Pointer,
  executable `error` + `recovery` kind/operation, no generic union diagnostic, no private
  path/capability leakage, and no source/cache/ledger/session lookup needed to
  form the next legal call;
- terminal recovery with `action=none` and no contradictory retry/inspect/
  continuation action; `same_operation` only when explicit `allowed_changes`
  makes the same-tool retry deterministic;
- Cortex-issued `task_ref`/`coordinator_ref`/`assignment_ref` values copied
  byte-for-byte with no session/host inference or value echo on a missing-ref
  failure;
- one same-child server-built bootstrap repair, full automatic briefing cursor
  pagination through `complete=true`, and exact same-child poll resumption for
  scalar and stable-option durable answers without question recreation;
- compact worker payloads with backend-derived identity and evidence;
- quarantine and fail-closed handling for legacy rows and lost capabilities;
- ContextCompiler and target-specific HandoffCompiler projections;
- public operation union of exactly nine operations and fixed audiences.

## Package and host checks

~~~bash
bash scripts/sync-cortex.sh --dry-run
python3 scripts/cortex-host-preflight.py --json
python3 scripts/probe-fresh-cortex-plugin.py
~~~

The first command is a no-write package preview. Host preflight and the fresh
plugin probe depend on the local Codex installation and may be unavailable in
source-only environments. A skipped or blocked host check is not a pass.

## Prompt checks

Prompt Contract v3 is the sole stable renderer. The offline lint and evaluator
must check the ownership matrix, section order, assignment-data boundary,
heading uniqueness, byte budgets, fence width, and deterministic fixtures.
There is one current fixture path and no comparison run.

## Manual review

Before release, inspect README.md, SECURITY.md, affected docs, plugin.json,
profiles.json, schemas, hooks, and marketplace metadata. Verify all links,
commands, version strings, operation names, and schema references. Ensure
private databases, caches, credentials, generated output, and temporary files
are absent from the package.

<!-- GENERATED:END -->
