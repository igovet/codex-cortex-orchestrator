# Release readiness

Status: source-mode release contract for Cortex 11.0.1.

## Current release identity

- release label: 11.0.1
- task and capability contract: v11
- SQLite schema: v18
- public facade: action-specific, alias-free MCP tools
- prompt contract: v3, sole stable renderer
- lifecycle hooks: 5

The v11 gate requires `start_orchestration` to be the sole task creator and
initial coordinator capability issuer. Coordinator calls carry private task
authority; worker calls preserve only their exact native dispatch authority.
Native execution must prove the exact
`spawn_agent` → exact `wait` → action-specific canonical wave read →
server-derived continuation sequence. Session/environment authorization,
`create_thread`, server-owned
CLI/executor launches, `repair_planning`, and manually authored
`advance`/`completions` are forbidden. Submitted-result repair uses only the
dedicated digest- and capsule-bound `repair_attempt` operation. Lost
capabilities and unknown histories fail closed.

The public response boundary is closed at v11. Every semantic action is a
separate MCP tool with its own complete closed one-level schema, and runtime
validation uses the same schema advertised by `tools/list`. Tool descriptions
are short semantics; skills and prompts duplicate no argument fields or schema
templates. There are no aliases or multiplexed action branches. Errors and
recovery provide bounded structured data for the named next operation. Heavy
state is available only through action-specific inspection. Only the exact
signed V11 v1--v8 lineage upgrades atomically to v18; historical authority
remains private and non-selectable, and unknown histories fail closed.

Worker dispatch validation occurs before any project or tool operation. A
missing or rejected native dispatch authority follows only server-returned same-child
recovery; no replacement
child or ambient inference is allowed. A successful `submit_attempt` is
terminal with no result-reference handoff, after which the
worker makes no further task-scoped calls. Invalid repair remains fail-closed.
Exact `CORTEX_ATTEMPT_FAILED retryable=false` is status only. The fixed
dispatch-scoped `finalize_worker_failure` transition is legal only after
structured `recovery.terminal_failure.evidence="server_bound"`; the server
verifies and consumes exact current evidence, while missing, stale,
wrong-dispatch, or replayed evidence is nonmutating.

The source tree is ready for a release decision only after the checks below
are run against the exact working tree. This page does not claim an installed
plugin, trusted hooks, or live-model access.

## Sole release gate

The current source tree has exactly one release gate and one test:

~~~bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v tests.test_marketplace_release_gate
~~~

That test composes the publishable package checks, bundled Python compilation,
marketplace validation, prompt-contract checks, and black-box MCP lifecycle
coverage into one source-mode result. There is no separate focused suite or
complete source suite. Prompt lint/evaluation, `git diff --check`, documentation
review, and the read-only commands below may provide supporting diagnostic
evidence, but they are not additional release gates or tests.

## Runtime acceptance

A worker must be able to checkpoint complete AttemptEvent rows of any content
volume and close one AttemptResult. The server must derive identity, timestamps, changed files,
checks, workspace observations, and verification metadata. The lifecycle must
distinguish WORK_COMPLETED, FINALIZING, COMPLETED, BLOCKED, and FAILED.

A failed projection or serializer after WORK_COMPLETED must retry the same
attempt. It must not create a replacement worker. ContextCompiler must produce
a complete immutable dispatch briefing, and HandoffCompiler must produce
target-specific implementation, QA, and review projections.

The public facade must expose the action-specific `tools/list` inventory with
fixed coordinator and worker audiences. No tool may multiplex unrelated
actions or expose aliases. MCP catalog schemas, runtime validation, and
`public_contracts.py` must remain identical.

## Supporting source diagnostics

Run the sole release gate above for the release decision. The following
commands are optional focused diagnostics for investigating or recording its
constituent checks; running them does not create additional release gates:

~~~bash
python3 scripts/validate-cortex-marketplace.py
python3 scripts/cortex-prompt-lint.py
python3 scripts/cortex-prompt-eval.py
bash scripts/sync-cortex.sh --dry-run
~~~

If host checks are available, also run:

~~~bash
python3 scripts/cortex-host-preflight.py --json
python3 scripts/probe-fresh-cortex-plugin.py
~~~

Record skipped or blocked host/live checks explicitly. Do not represent a
source-mode result as proof of an installed cache or hook trust.

## Evidence handoff

The release handoff records:

- exact git revision and working-tree status;
- commands, environment, and test counts;
- package and prompt validator output summaries;
- schema, manifest, MCP catalog/runtime parity, and hook-set verification;
- links and Markdown review status;
- unavailable host or live-model checks and their reason.

<!-- END RELEASE READINESS -->
