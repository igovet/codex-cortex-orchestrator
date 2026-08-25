# Release readiness

Status: source-mode release contract for Cortex 11.0.1.

## Current release identity

- release label: 11.0.1
- task and capability contract: v11
- SQLite schema: v17
- public operations: 9
- prompt contract: v3, sole stable renderer
- lifecycle hooks: 5

The v11 gate requires `start_orchestration` to be the sole task creator and
initial coordinator capability issuer. Coordinator calls must carry
`task_ref`/`coordinator_ref`; worker calls must carry
`task_ref`/`assignment_ref`. Native execution must prove the exact
`spawn_agent` → `wait` → `read_worker_result` → server-derived continuation
sequence. Session/environment authorization, `create_thread`, server-owned
CLI/executor launches, `repair_planning`, and manually authored
`advance`/`completions` are forbidden. Plan and outcome repair must use only a
digest- and capsule-bound patch through `complete_attempt`. Legacy rows and
lost capabilities fail closed; incompatible old namespaces are quarantined.

The public response boundary is closed at v11. Lifecycle and governance
responses use typed action, receipt, inspection, and top-level error/recovery unions;
worker briefing, question, event, completion, and result responses carry only
their minimal canonical fields. Generic `user_message`, `user_view`,
`internal`, full pipeline/governance state, and prose `next_action` fields are
not public outputs. Patch-critical diagnostics, original JSON Pointer paths,
exact semantic repair pointers, bounded nested field schemas,
`error={code,category,message,diagnostics}`, and explicit
`recovery={kind,operation,retryable,state_mutated}`. Repair recovery retains
opaque handles, base payload digests, and allowed
patch operations/paths are retained. A
malformed handle copy reissues the same repair; a correctly shaped handle that
fails integrity is terminal.
Heavy state is available only through explicit inspect. The exact canonical
v16 predecessor is quarantined as a complete unit with no state adoption into
v17; v15 and older or unknown namespaces fail closed without archival.

Worker bootstrap validates its required briefing and capability references before
any project or tool operation. A missing pair permits at most one same-child
repair follow-up using the exact server-retained references; no replacement
child or ambient inference is allowed. A successful `complete_attempt` returns
`terminal: true` with no result-reference handoff, after which the
worker makes no further task-scoped calls. Invalid repair remains fail-closed.
Exact `CORTEX_ATTEMPT_FAILED retryable=false` is status only. The fixed
dispatch-scoped `finalize_worker_failure` transition is legal only after
structured `recovery.terminal_failure.evidence="server_bound"`; the server
verifies and consumes exact current evidence, while missing, stale,
wrong-dispatch, or replayed evidence is nonmutating.

The source tree is ready for a release decision only after the checks below
are run against the exact working tree. This page does not claim an installed
plugin, trusted hooks, or live-model access.

## Blocking gates

1. Python 3.11+ compilation succeeds for bundled scripts.
2. Marketplace validation accepts the root plugin entry, manifest, MCP
   configuration, launcher, profiles, skills, hooks, and schema.
3. Prompt lint and deterministic evaluation pass for the v3 renderer.
4. Focused protocol, facade, context, handoff, governance, lifecycle, and
   packaging tests pass.
5. The complete source test suite passes with exact counts recorded.
6. git diff --check is clean.
7. README.md, SECURITY.md, and every affected Markdown page has current links,
   commands, version identity, and schema references.
8. No secrets, private state, caches, or generated temporary files are included.

## Runtime acceptance

A worker must be able to checkpoint complete AttemptEvent rows of any content
volume and close one AttemptResult. The server must derive identity, timestamps, changed files,
checks, workspace observations, and verification metadata. The lifecycle must
distinguish WORK_COMPLETED, FINALIZING, COMPLETED, BLOCKED, and FAILED.

A failed projection or serializer after WORK_COMPLETED must retry the same
attempt. It must not create a replacement worker. ContextCompiler must produce
a complete immutable dispatch briefing, and HandoffCompiler must produce
target-specific implementation, QA, and review projections.

The public facade must expose exactly nine operations. Coordinator and worker
audiences are fixed at launch, and both use read_worker_result for scoped
result access. Cross-stage links are limited to attempt_result_ref,
context_result_refs, and predecessor_result_refs.

## Package checks

~~~bash
python3 scripts/validate-cortex-marketplace.py
python3 scripts/cortex-prompt-lint.py
python3 scripts/cortex-prompt-eval.py
bash scripts/sync-cortex.sh --dry-run
python3 -m pytest -q
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
- schema, manifest, public-operation, and hook-set verification;
- links and Markdown review status;
- unavailable host or live-model checks and their reason.

<!-- END RELEASE READINESS -->
