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
It retains only timestamp, thread lineage, observed role/model/effort, safe spawn
selectors, tool name, argument/result digests, bounded routing selectors and safe
error codes. Review
every call for a concrete need and correct role ownership. The final `audit` rejects
unsuccessful MCP events, Cortex call errors or truncation, missing Cortex outcomes,
forbidden coordinator, plugin, or draft access, worker calls after publication without a successful explicit parent follow-up
after the final handoff, handoffs missing the latest same-worker publication or
including unknown/other-worker report references, oversized document pages and command sessions without a terminal
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
managed updates preserve unrelated profiles. This optional personal export check is separate from marketplace preparation;
live preparation must not install these exports.

Marketplace parity requires a personal agents directory with no Cortex TOMLs;
`prepare_codex.py` must only install the plugin through the marketplace. Verify
that each native worker loads its complete packaged skill by native injection or
its exact advertised SKILL.md path, then publishes its own report. An MCP-only pass
or a dev-only native registry does not prove ordinary installation.

Targeted skill/declared Markdown reference reads are valid instruction loading;
TOML, server internals, broad enumeration and mixed read/side-effect commands remain
violations. Review full loading evidence and truncation, not merely a role label.
Encrypted assignment content remains opaque. See [host compatibility](host-compatibility.md).
A standalone wrapper text item `exit_status=N`
is an explicit command receipt; stdout containing that string is not sufficient.

CLI submission uses one named tmux buffer and bracketed paste, preserving internal
newlines, indentation and repeated spaces. Receipt matching no longer collapses
whitespace. Only trailing line terminators from the prompt file are removed.

The isolated live observer compares a private SHA-256 of the submitted task text
with the request retained by task creation. It ignores only the leading route
token and outer whitespace; added host envelopes, translations and internal
formatting changes fail qualification. Diagnostics expose only the comparison,
not the request. CLI submission preserves multiline text with a single bracketed
paste, and resume preserves the original comparison reference. Worker instructions
require complete loaded skills and structured command receipts. Exact advertised
skill and needed declared Markdown reference reads are valid; installation exploration,
TOML and server-internal reads are forbidden. Printed shell markers alone are not
success evidence. A successful backend execution does not excuse a wrapper
that exposes only stdout: the observer retains `wrapper_outcome=unverified` and
reports `command_wrapper_missing_receipt`, including for skill reads.

Live qualification also rejects delegation on a newly created task before the
coordinator has received a successful pipeline publication receipt. A created
or edited draft is not a published pipeline; discovery may revise the initial
pipeline after delegation, but cannot precede its publication.

Desktop rich-text formatting is accepted only when independently present in the
recorded user message and traceable to the prepared prompt: a blank line before
an ordered list and literal underscore escaping. Other spacing and source changes
remain failures. This exception does not apply to CLI or unobserved model changes.

Native-source tests exercise the real read-only host index and typed rollout
records: exact current-turn selection, thread/project isolation, bounded tails,
symlink rejection, absent-source failure, literal credential redaction and replay
without a remaining host source. Live creation receipts now expose the server's
stored-source digest; the observer compares that receipt against independently
recorded input rather than a model-authored request argument.

Steering tests cover multiple queued corrections, exact whitespace and Unicode,
restart, repeated native receipts, operation replay with new input, transactional
rollback, source rewrite, partial host records, foreign-thread exclusion and
literal credential redaction. Real-host qualification must additionally compare
archived source against native UserMessage receipts across steering and resume.

The passive call audit records typed native coordinator user-input events without
message bodies. A new user event permits delivery of steering after a native wait
and invalidates a previous unchanged-catalogue observation. An unrelated injected
user-role envelope does not provide that permission.

Decision-brief checks allow a selected ordinary report start page for the coordinator,
reject its continuation pages and preserve forbidden project-tool checks. Follow-up
checks resolve the exact target in the observed native lineage, require prior report
publication and final handoff, and retain command/session accounting for each assignment.
Failed delivery, a plain message, an unrelated target or a report from another worker
never reopens ownership. Role suitability and independent review remain model decisions.
On resume, prior successful publication receipts and matching final handoffs reduce
to metadata-only assignment snapshots. Old calls are not replayed as current-run
evidence. An intervening worker action invalidates the completed snapshot until a
new publication and handoff are observed. Intact worker context may retain its schemas;
actual context loss still requires the documented recovery procedure.

Use the separate [outcome suite](quality-evaluation.md) to measure quality and cost;
protocol qualification alone does not demonstrate improved task-solving ability.

The bounded ASCII fixture steering route has an exact native receipt check. A prior
run changed a typed Latin character to a Cyrillic lookalike and was rejected; after
allowing composer focus to settle and slowing synthetic typing, the same fixture
text arrived exactly in the real Desktop task. This is evidence for this host route,
not a guarantee for arbitrary keyboard layouts. Inspect the actual message before
any further input after failure; never replay an uncertain submission. Visually confirm
the empty composer and use `scripts/cortex-desktop-dev steer --prompt-file FILE
--composer-x X --composer-y Y` with coordinates observed inside that exact window.
The helper checks the isolated PID, types once and submits with Ctrl+Enter, then
requires one exact typed UserMessage receipt on the retained task, allowing only the
observed single editor-added paragraph newline. It does not infer
readiness or semantic acceptance, change the clipboard, or replay uncertain input.
Non-ASCII follow-up transport is unverified; the initial prepared-prompt route keeps
its existing Unicode behavior. Initial submission still uses `send` and verifies a
new task receipt.

Audit regressions also cover an active worker message followed by one coordinator
reply after a wait. A reply to a different worker, a duplicate reply or a failed
inbound message must not receive that exception. Message bodies remain private.

Patch-access regressions distinguish evidence text mentioning a skill path from
actual add/update/delete/move targets inside the installed plugin. The observation
layer records accesses and outcomes; report prose alone is not filesystem access.

Concurrent report-read correlation uses retained literal report identities where
available, including a single local const binding. Ambiguous intervals or bindings
remain separate evidence rather than being matched by call order.

Generated skill boundary checks compare the declared line count with the complete
file and require one final completion marker. The full profile body must still
match its generated TOML export; framing cannot remove any role or protocol text.

A successful native MCP receipt must not inherit a later JavaScript consumer error
as an MCP-error policy flag. The consumer failure remains in the complete audit;
a genuine MCP error or absent receipt is not exempted.
