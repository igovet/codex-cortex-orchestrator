# Release readiness

Status: source-mode release contract for Cortex 11.0.1.

## Current release identity

- release label: 11.0.1
- task and capability contract: v11
- SQLite schema: v19
- public facade: action-specific, alias-free MCP tools
- prompt contract: v3, sole stable renderer
- lifecycle hooks: declared by `plugins/cortex/hooks/hooks.json`

The v11 gate requires `start_orchestration` to be the sole task creator and
initial coordinator capability issuer. Coordinator calls carry private task
authority; worker calls preserve only their exact native dispatch authority.
Native execution must prove the exact
native V2 `spawn_agent` → generic timeout-bounded `wait_agent` cycles →
canonical terminal results plus exact matching terminal Stops →
action-specific canonical wave read and server-derived continuation sequence.
There is no exact-child wait target, and early, timed-out, steered, partial, or
unrelated ordinary wake-ups authorize no read. Ordinary progress waits use a
300-second timeout. The directed confirmation is armed only after all terminal
facts are durable and qualifies only when the structured timeout boolean is
true; message text is ignored. Activity, steering, or another
wake-up repeats the short confirmation. This is same-user local trust, not
cryptographic or server attestation; unknown or disabled hook state fails
closed.
After a completed wave is read, make one evidence-frontier decision.
`revise_future_pipeline` changes only unexecuted future work;
`append_rework_wave` appends product correction and independent verification
for a completed canonical result. Technical failure uses neither operation: the
server owns the exact-occurrence Luna-to-Terra-to-Sol replacement ladder and
capability-safe profile resolution. Every newly returned worker repeats the
native barrier, and required governance closure executes before final handoff.
Session/environment authorization,
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
state is available only through action-specific inspection. Fresh state uses
one compact schema-v19 ledger. Exact signed released schema-v17 and schema-v18
histories upgrade transactionally in place while retaining their append-only
migration rows before the schema-v19 row is appended. The exact signed legacy
V1--V8 namespace is archived privately before a fresh schema-v19 ledger is
created, without migrating or exposing its task authority. Unknown, incomplete,
unsigned, reordered, or tampered histories fail closed and are not automatically
quarantined, guessed, or adopted.

Worker dispatch validation occurs before any project or tool operation. If a
worker first operation is pending trusted spawn observation, it retries only
that operation with bounded backoff until a finite deadline, with no project
access or replacement. A successful exact retry automatically clears the
transient observer failure. At the deadline, or for other missing or rejected native
dispatch authority, only public fail-closed recovery applies. A successful
`submit_attempt` is
terminal with no result-reference handoff, and the worker then makes no further
task-scoped calls. Backend worker-wave reads and continuation remain
unavailable until every bound child has a canonical terminal result and exact
matching terminal Stop. The coordinator never
supplies lifecycle evidence or inspects plugin or private state. Invalid repair
remains fail-closed. A nonretryable worker final is status only. Terminal
failure finalization is legal only when public structured recovery explicitly
directs it for the original native dispatch; Cortex verifies and consumes the
private current binding, while missing, stale, wrong-dispatch, or replayed
recovery is nonmutating. Native prose is never parsed into authority.

After a completed wave is read, the single decision for that canonical evidence
frontier may change only unexecuted future waves. Every newly returned worker repeats native spawn,
ordinary progress waits, matching terminal Stop, and canonical wave
read; required governance closure executes before final handoff.

The source tree is ready for a release decision only after the checks below
are run against the exact working tree. This page does not claim an installed
plugin, trusted hooks, or live-model access.

## Sole release gate

The current source tree has exactly one standalone marketplace/runtime
publishability gate and no unit-test suite:

~~~bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q tests/test_marketplace_release_gate.py
~~~

That single test first builds the exact allowlisted working-tree candidate with
the shared release-candidate builder, validates that package from its own
copied files, and then runs bundled Python compilation, marketplace validation,
prompt-contract checks, and black-box MCP lifecycle coverage against it. There
is no separate focused suite or complete source suite. Prompt lint/evaluation,
`git diff --check`, documentation review, and the read-only commands below may
provide supporting diagnostic evidence, but they are not additional release
gates or tests.

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
python3 scripts/verify-cortex-release.py --mode source
python3 scripts/verify-cortex-release.py --mode head
python3 scripts/cortex-prompt-lint.py
python3 scripts/cortex-prompt-eval.py
bash scripts/sync-cortex.sh --dry-run
~~~

Source mode copies only the public release files, manifest-selected plugin
assets and profiles, exact bundled skills, reachable public documentation, and
the recursively resolved local Python import closure. Head mode additionally
fails unless every required candidate file is tracked and byte-identical to
HEAD; it never substitutes an older `git archive HEAD` for dirty or untracked
installable source.

If host checks are available, also run:

~~~bash
python3 scripts/cortex-host-preflight.py --json
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
