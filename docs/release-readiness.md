# Release readiness

Status: source-mode release contract for Cortex 10.0.6.

## Current release identity

- plugin version: 10.0.6
- task contract: cortex/v10
- orchestration lifecycle: cortex/orchestration/v6
- SQLite schema: v15
- public operations: 9
- prompt contract: v3, sole stable renderer
- lifecycle hooks: 6

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

A worker must be able to checkpoint bounded AttemptEvent rows and close one
AttemptResult. The server must derive identity, timestamps, changed files,
checks, workspace observations, and verification metadata. The lifecycle must
distinguish WORK_COMPLETED, FINALIZING, COMPLETED, BLOCKED, and FAILED.

A failed projection or serializer after WORK_COMPLETED must retry the same
attempt. It must not create a replacement worker. ContextCompiler must produce
a bounded immutable dispatch briefing, and HandoffCompiler must produce
target-specific implementation, QA, and review projections.

The public facade must expose exactly nine operations. Coordinator and worker
audiences are fixed at launch, and both use read_worker_result for bounded
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
