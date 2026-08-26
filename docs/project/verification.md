# Verification index

<!-- GENERATED:START -->

This page describes source-mode checks for Cortex 11.0.1 and the v11 task and
capability contract on SQLite schema v19. A command is evidence only
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

Run the sole release gate after the static checks:

~~~bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q tests/test_marketplace_release_gate.py
~~~

Record the release-gate outcome, exact command, and environment. The current
prompt evaluators are `scripts/cortex-prompt-eval.py` and the opt-in
`scripts/cortex-prompt-live-eval.py --live`; do not treat retired fixture suites
as release evidence.

## Protocol acceptance

Focused protocol checks must cover:

- AttemptEvent append-only ordering, stable event keys, bounds, and retries;
- AttemptResult semantic payload validation and server-owned metadata;
- RUNNING → WORK_COMPLETED → FINALIZING → COMPLETED;
- explicit BLOCKED and FAILED outcomes;
- same-attempt finalization after view, serialization, or infrastructure failure;
- server-derived changed files, checks, timestamps, identity, and observations;
- exact task/attempt/result scope for briefing and predecessor-result reads;
- private coordinator task authority and exact native worker dispatch authority;
- native V2 `spawn_agent`/generic timeout-bounded `wait_agent`/canonical
  terminal result/exact terminal Stop/wave-read/continue ordering, including
  parallel workers and 300-second ordinary progress waits. Host-owned identity
  binding joins each authorized worker call to its exact native child; no exact-child
  wait target, no prose or wait-output parsing, unknown or disabled hooks
  failing closed, and no model inspection of private state,
  session/environment authorization, or manually authored advance/completions;
- one evidence-frontier decision per completed canonical wave:
  `revise_future_pipeline` only for unexecuted future work,
  `append_rework_wave` only for product correction of a completed result, and
  server-owned Luna-to-Terra-to-Sol technical replacement with capability-safe
  profile resolution; every new worker repeats the lifecycle and required
  governance closure precedes final handoff;
- coordinator-model ownership of the worker waves submitted at start,
  with backend validation/dispatch but no server-selected replacement pipeline;
- one action per MCP tool, with no multiplexed selector, branch registry,
  compatibility alias, or audience-dependent shared request shape;
- every advertised tool owning a complete closed one-level contract, with
  runtime validation consuming that same contract;
- short semantic tool descriptions and no argument fields, constraints, or
  schema templates duplicated in skills or prompts;
- separate `submit_attempt` and `repair_attempt` operations, with repair bound
  to the same capsule, digest, retained draft, and attempt;
- malformed calls producing bounded diagnostics and deterministic structured
  recovery without private path/capability leakage or source/cache/ledger/session lookup;
- Cortex-issued coordinator and worker authority copied byte-for-byte
  with no session/host inference or value echo on a missing-authority failure;
- bounded same-operation backoff until a finite deadline when a worker first
  operation precedes trusted spawn observation, with zero project access and no
  replacement and automatic transient-observer clearing after an exact
  successful retry; complete briefing pagination; and a genuine durable
  question pause followed by real-answer recording and exact same-worker
  resumption in a new native turn without recreation;
- server-fixed opaque continuation paging for every growing read; fixed
  receipts and repair cards do not paginate;
- compact language-neutral worker text/report payloads with backend-derived
  identity and evidence, server-owned known-locale/canonical-fallback display,
  and no language blocker or `label_en`/localized alias;
- transactional in-place upgrade of exact signed released schema-v17/schema-v18
  histories to v19 with append-only rows preserved, including exact Unicode
  pending/answered question migration, conservative category assignment,
  category/answer digest recomputation, second-open idempotence, and rollback;
  run `python3 scripts/probe-cortex-question-migration.py` for the direct signed
  V17/V18 database probe. Private archival and fresh v19 bootstrap remain the
  route for the exact signed legacy V1--V8 namespace; unknown history and lost
  capability handling fail closed;
- ContextCompiler and target-specific HandoffCompiler projections;
- action-specific `tools/list` inventory and fixed audiences.

## Package and host checks

~~~bash
./scripts/sync-cortex.sh --dry-run
python3 scripts/cortex-host-preflight.py --json
~~~

The first command is the sole supported installation/update path in no-write
preview mode. Host preflight depends on the local Codex installation and may
be unavailable in source-only environments. Do not substitute Marketplace or
direct `codex plugin` commands. A skipped or blocked host check is not a pass.

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
