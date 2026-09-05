# Verification

Source checks require Python 3.11+ with `pytest` and `jsonschema` installed.
They run sequentially on a current content-stamped payload:

```bash
python3 -B scripts/cortex_package.py stamp
python3 -B scripts/validate-cortex-marketplace.py
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q
./scripts/sync-cortex.sh --check
git diff --check
```

The focused suite covers seven advertised operations, original request recovery,
advisory governance, real Markdown files, a single newest-first pipeline,
immutability, duplicate delivery, changed-delivery conflicts, task isolation,
concurrency, file publication recovery, Unicode cursor reads, stable newest-first
catalogue snapshots, process restart, safe actionable errors, input/output schema conformance,
whole-file publication beyond 16 MB, file-backed pipeline publication beyond the MCP
request limit, typed server-created drafts, per-thread draft ownership, source cleanup,
disk failure, short reference collision recovery,
complete native profile payloads, host-metadata task binding, nested parent
inheritance, automatic pipeline selection and package identity.

For real execution, use a separate existing test project and start
`./scripts/cortex-live-smoke start --workdir PATH`. The helper creates only the
exact `cortex-markdown-smoke` session on the default tmux server and attaches an
owner-private output-only observer before entering `scripts/cortex-dev`.
That entry point refreshes and installs only `$HOME/.cortex-dev/.codex`.

Inspect `capture` and `status`. A visible trust screen permits one explicit
`enter`; then visibly confirm the composer. Inspect `events` for the passive
server initialization receipt and compare package path, version and catalogue
digest with the candidate. A receipt is observation, never task authority.

Send a prompt file beginning with the actual `$cortex:orchestrator` skill token
and an ordinary product request. Use a substantial landing-page brief for multistage
acceptance, without teaching the model orchestration steps in the prompt. `send --prompt-file FILE` inserts the
complete literal text, waits five seconds and sends one named Enter. It does
not infer acceptance. Observe task creation, pipeline writing, native delegation,
selective report reading and worker publication. Send a concrete product change;
verify the same task and pipeline identity, newest edition first, added work and
final result. Inspect metadata-only `events` for all worker calls and errors.
Use `calls` for the complete coordinator/worker host history and run `audit`
before accepting or stopping the CLI session. The CLI commands share the Desktop
observer, policy checks and command-session accounting.
Before adopting a new host-context mechanism, observe the actual incoming
coordinator and child metadata on both CLI and Desktop first. Verify exact parent
linkage; never infer support from upstream code alone.

After exiting ordinary Codex, capture `Cortex live-dev exit=0`, then `stop` the
exact session. Resume with the same workdir and `--resume-last`; confirm the
existing task, selective report recovery and no replacement task creation.
Finish by stopping the exact session; after failure use `stop --interrupt`.
Never kill the tmux server or use `codex exec` as native evidence.

Actual Desktop uses
`scripts/cortex-desktop-dev start --workdir PATH --prompt-file TASK_PROMPT.txt --data-dir FRESH_PRIVATE_DIR`
with `CORTEX_DESKTOP_BINARY` pointing to the real executable. The helper prepares
the same candidate, uses a disposable Electron profile, and opens exactly one new
Desktop task with the literal prompt from the regular UTF-8 file. The explicit
owner-private data directory keeps a run inspectable and prevents an obsolete
isolated schema from contaminating fresh qualification; without it the helper
creates and later removes a fresh private directory automatically. Omit
`--prompt-file` only for manual UI entry. Source checks are not Desktop evidence;
disclose missing credentials, display access or unrun host checks. See the
recorded [release evidence](../release-readiness.md).

After the dev window is visible and the prepared composer is confirmed, run
`scripts/cortex-desktop-dev send`. It resolves exactly one visible ChatGPT window
whose X11 PID equals the isolated process recorded by `start`, rechecks ownership,
submits once, and refuses duplicate submission. It never targets another
Desktop instance by title alone.

The Desktop `send` command focuses the prepared composer, uses `Ctrl+Enter`, and
acknowledges delivery only after exactly one new task appears in isolated host state.
Use `scripts/cortex-desktop-dev events` while the task runs and
`scripts/cortex-desktop-dev audit` before accepting it. The audit consumes the
complete private metadata journal from every coordinator and worker MCP process
and fails if any non-initialization operation has an outcome other than `success`.
Read events identify pipeline versus ordinary report, the short selected report ID,
and start versus continuation without logging cursors, Markdown, request text or
other arguments. This makes selective worker reads directly auditable.

Use `scripts/cortex-desktop-dev calls` to inspect every host-tool invocation from
the coordinator and native workers. With no `--limit`, it emits the full run:
wrappers, each statically visible nested host call, and each actual Cortex MCP event.
It also records every host `CommandExecution` item separately, so one wrapper that
starts several commands cannot hide an individual nonzero exit or truncated result.
It retains only timestamp, thread lineage, attached role/model/effort, safe spawn
selectors, tool name, argument/result digests, bounded routing selectors and safe
error codes. Review
every call for a concrete need and correct role ownership. The final `audit` rejects
unsuccessful MCP events, Cortex call errors or truncation, missing Cortex outcomes,
forbidden coordinator, plugin, or draft access, worker calls after a successful
`write_report`, oversized document pages and command sessions without a terminal
result. It also rejects a second mutation worker with the same active profile before
the first owner publishes its terminal report. Every tool error remains in the
chronological `tool_error_history`; its Cortex subset fails the run, so a corrected
`create_draft` parameter failure cannot disappear behind its successful retry. Unsafe
JavaScript template literals around Cortex draft patches are also rejected. The audit
also rejects routine report-catalogue and pipeline reads by fresh workers, making
selective report routing directly visible. An exact successful command retry records
the earlier host-check failure under `resolved_host_failures`; it is never erased.
If a run must be rejected, collect the complete current `calls` and `events`
and execute `audit` before stopping that isolated run. One early fault must not hide
other calls that were already dispatched.

Browser-dependent workers run sequentially when the host exposes one shared browser
surface. Each worker uses only tabs and command sessions created in its own native
thread; the live audit rejects process hunting and unresolved cross-thread resource
failures instead of accepting a later summary as proof.


Both ordinary CLI and actual Desktop scenarios are required on the same unchanged
payload. Exercise a genuine question with detailed context, answer alternatives
and consequences as ordinary chat text, then incorporate the user's answer. The PR workflow runs only for pull requests targeting
dev or main; automated source tests do not substitute for either real host.

Live qualification assesses Cortex coordination, not the LLM's development ability.
Project command and browser failures remain visible in separate diagnostic fields;
they do not independently fail Cortex acceptance. Draft-edit failures, invalid MCP
calls, report ownership violations, duplicate publications, and unresolved sessions
at report publication do fail it. Do not describe project diagnostics as Cortex faults.

Resume observation retains the original thread-creation boundary while selecting
only rollout calls made after the resumed session started. Prompt receipts include
the existing coordinator thread. Old calls are neither lost nor mistaken for new
MCP deliveries; the previous run's audit remains separate.

Native-profile installation regression checks must begin with an empty agents
registry. Verify that the package alone reports 22 missing profiles, explicit setup
registers exact bytes, conflicting user files prevent all writes, and subsequent
managed updates preserve unrelated profiles. Isolated live preparation must call
this same packaged setup, not maintain its own privileged copying path.
