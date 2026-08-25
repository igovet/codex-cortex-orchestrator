# Verification index

<!-- GENERATED:START -->

This page describes source-mode checks for Cortex 11.0.1 and the v11 task and
capability contract on SQLite schema v18. A command is evidence only
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
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v tests.test_marketplace_release_gate
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
- native `spawn_agent`/exact `wait`/action-specific wave-read/continue ordering, with no
  session/environment authorization or manually authored advance/completions;
- coordinator-model ownership of the worker waves submitted at start,
  with backend validation/dispatch but no server-selected replacement pipeline;
- one action per MCP tool, with no multiplexed selector, branch registry,
  compatibility alias, or audience-dependent shared request shape;
- every advertised tool owning a complete closed one-level `inputSchema` from
  `public_contracts.py`, with runtime validation consuming that same schema;
- short semantic tool descriptions and no argument fields, constraints, or
  schema templates duplicated in skills or prompts;
- separate `submit_attempt` and `repair_attempt` operations, with repair bound
  to the same capsule, digest, retained draft, and attempt;
- malformed calls producing bounded diagnostics and deterministic structured
  recovery without private path/capability leakage or source/cache/ledger/session lookup;
- Cortex-issued coordinator and worker authority copied byte-for-byte
  with no session/host inference or value echo on a missing-authority failure;
- server-returned same-child recovery, complete briefing pagination, and exact
  same-child question/answer resumption without question recreation;
- server-fixed opaque `c11p` cursor paging for every growing read; fixed
  receipts and repair cards do not paginate;
- compact language-neutral worker text/report payloads with backend-derived
  identity and evidence, server-owned known-locale/canonical-fallback display,
  and no language blocker or `label_en`/localized alias;
- exact signed V11 v1--v8 lineage upgrade atomically to v18, private
  non-selectable old task authority, and fail-closed unknown history/lost
  capability handling;
- ContextCompiler and target-specific HandoffCompiler projections;
- action-specific `tools/list` inventory and fixed audiences.

## Package and host checks

~~~bash
./scripts/sync-cortex.sh --dry-run
python3 scripts/cortex-host-preflight.py --json
python3 scripts/probe-fresh-cortex-plugin.py
~~~

The first command is the sole supported installation/update path in no-write
preview mode. Host preflight and the fresh
plugin probe depend on the local Codex installation and may be unavailable in
source-only environments. Do not substitute Marketplace or direct `codex
plugin` commands. A skipped or blocked host check is not a pass.

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
