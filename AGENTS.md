# Cortex repository development

Use `python3`. Runtime behavior belongs entirely below `plugins/cortex/`.
Do not activate orchestration merely because this repository is named Cortex;
only an explicit user selection activates the bundled orchestrator skill.

The server stores tasks, advisory governance and Markdown reports. The model
owns the pipeline, native subagents, steering, evidence interpretation and
completion. Keep tool argument contracts in the advertised schemas and property
descriptions only, never in skills or profile instructions. Preserve all 22
specialist profiles and their shared report protocol. Do not introduce
compatibility routes, mandatory stages or approval machines. Lifecycle hooks may
perform short local source-memory, observation and exact registered-file integrity
work; they never choose agents or decide task acceptance.

Throughout design, implementation, qualification, and live work, use only
capabilities that are actually supported and observable in the currently installed
Codex and host version. Before relying on a capability, verify that it exists in
that current host. Do not require hypothetical or future host APIs, typed contexts,
receipts, hooks, enforcement, models, tools, or transports. If a needed capability
is absent, redesign within current-host mechanisms or report the work genuinely
impossible; do not wait for or request a new host, synthesize evidence, claim
`host_enforced`, or mark an attempt ACCEPT. Tests and acceptance scenarios must
themselves be executable on the currently installed Codex. These constraints do
not relax the CLI-before-Desktop rule below.

For the explicit current-host observational qualification mode, the absence of a
signal the installed host cannot emit is `not_applicable` or a retained
diagnostic, never by itself a BLOCK: this includes bootstrap attestation,
assignment-policy/skill-load receipts, a separate server event, and a process
self-exit marker. `observational_accept` still requires complete, non-truncated
capture/calls/events/audit plus one exact successful public task binding, one
completed native worker, one task-bound worker report, and exact coordinator
artifact reconciliation. BLOCK only for an actual task failure; pending or
incomplete work; malformed, mismatched, ambiguous, replayed, or truncated
evidence; artifact/check failure; or observed unsafe private/project mutation.
When the installed host omits a public task ID or native-result body, the outcome
may use only one expected root, one parent-bound native worker/report, and one
exact non-replayed artifact reconciliation as opaque observational binding;
missing, duplicate, or mismatched links remain BLOCK.
For an interactive CLI without a self-exit marker, verified idle composer,
empty owned sessions, completed native outcome, and exact owned cleanup receipt
are the alternative terminal proof. This is not host enforcement and must retain
`host_enforcement_state=unverified`.
In that same mode, a redundant worker-final report-ID omission is diagnostic when
exactly one task/parent/profile-correlated worker publication and coordinator
artifact reconciliation prove the report identity. One completed, non-mutating
`read_report` `invalid_arguments` lookup is likewise diagnostic when the valid
publication and reconciliation are independently present. Neither exception
permits missing/ambiguous publication, a failed write, a mismatch, replay or
truncation, mutation, or an unsafe/private target.
One or more exact-equivalent denied-before-dispatch errors from the bounded
registered worker `SKILL.md` read are diagnostics only after that same complete
outcome proof. They must share worker/profile/manifest-bound static-read identity;
any differing, replayed, private/project, mutating, dispatched, truncated, or
ambiguous read remains blocking and none credits a skill receipt.
The current interactive transport may use exactly one literal `bash -lc` envelope
around that read; the observer must revalidate its sole payload against the same
closed static-read grammar. Nested shells, extra commands, paths other than the
registered leaf, directories, globs, and every write remain forbidden access.
In `current_host_mcp_first` only,
`worker_assignment_policy_unverified` and `mcp_first_bootstrap_unverified` are
always printed `unsupported_by_current_host`, `not_applicable` diagnostics. The
installed host cannot emit those attestations; they never become integrity
invalidators or hard blockers, grant no acceptance evidence or score, and retain
`host_enforcement_state=unverified`. All supported observable outcome, safety,
completion, artifact, replay, truncation, ambiguity, pending/open, and mutation
requirements remain fail-closed.
The final current-host audit boundary reapplies this exact two-label rule after
all parser and classifier contributions, so an earlier representation cannot
reintroduce either unavailable attestation as an invalidator. It changes no
other policy row or supported-evidence requirement.
Generic `pre_binding_host_action` is never demoted by the outcome partition because
it has no strict actor/profile/path proof and may describe an unsafe dispatch. The
only coordinator setup exception is an observer-proven, manifest-bound, bounded,
read-only literal `skills/orchestrator/SKILL.md` or its declared instruction-reference
read with its active-skill marker and exact installed-candidate identity;
the same exact read-only proof covers its bundled communication, tool-discipline,
content-safety and recovery companions. Up to four literal instruction leaves may
be read in one bounded shell command; no arbitrary skill or cache path qualifies.
the separate registered-worker static-SKILL predicate can retain independently
complete, read-only denied records as diagnostics. Neither exception admits a
directory, glob, different skill, unknown provenance, private/project target,
mutation, dispatch, truncation, replay, or ambiguity.
They remain printed and do not waive any task/report/artifact/pending failure or
any other policy label; outside that mode normal policy handling applies.
The current-host audit exits 0 only when it prints `observational_accept` with
valid evidence and every remaining finding is an explicit retained diagnostic or
not-applicable signal. It exits 1 for every hard failure, policy row, pending or
open state, mismatch, mutation, truncation, ambiguity, replay, or missing outcome
proof; diagnostics remain printed in either case.

Use one real `pipeline.md` per task, newest edition first with older editions
below. Other reports are immutable real Markdown files. Store report bodies in
`.codex/cortex/<task>/` in the project and only metadata/relations in SQLite.
Both the newest-first catalogue and report content use bounded cursor reads.
Workers select relevant reports rather than reading everything.

Read relevant docs/project and docs/features pages before nontrivial work.
After behavior changes update README.md, SECURITY.md and affected documentation;
check links and commands against source. Keep secrets, private reports and raw
host logs out of repository documents and diagnostics. Report unrun checks.

For this release use semantic version 1.15.9. After any plugin payload
edit, regenerate its complete content hash with
`python3 -B scripts/cortex_package.py stamp` before release-sensitive checks.
Run package, sync and test checks sequentially in the same checkout. Use the
smallest meaningful tests first. Source-only sync uses `--check` or `--dry-run`.

Never install or update the stable user's Cortex plugin. Live preparation must
use `./scripts/cortex-dev` (or the root forwarding wrapper), which alone prepares
and installs the exact isolated `$HOME/.cortex-dev/.codex` candidate. No direct
normal sync, ad hoc cachebuster or stable configuration changes are allowed.

All live-dev tests use `gpt-5.6-luna` with `high` effort for the coordinator.
Native test workers also use Luna, at medium or high effort; heavy models are
prohibited in live CLI/Desktop tests. Keep this test policy in isolated launcher
configuration, without changing stable user settings or general plugin routing.

After any repository work, live-dev verification is mandatory in both real
environments, in this order: run a focused CLI scenario first, then run Desktop
only after the CLI is ACCEPT with a clean audit on the same unchanged isolated
candidate and payload. CLI and Desktop are both required; neither substitutes for
the other. If CLI fails, is rejected, unavailable, or unverified, end that live
attempt, retain the complete current calls/events tails and audit, stop its exact
session, and do not start Desktop as a diagnostic substitute. Report that host as
unverified and correct/requalify the candidate before a new CLI-first attempt.

This release-verification obligation belongs to the owner of the Cortex change.
Run live workloads in a separate isolated project, never in this Cortex checkout.
Keep Cortex implementation and source checks in this checkout; the disposable
project has its own source, Git history and task instructions.
An ordinary research task or generated proof note inside a live scenario is the
workload being observed, not another Cortex change: complete that deliverable
without recursively launching CLI/Desktop qualification or preparing a candidate.
The outer owner still must finish both real-host checks; this scope distinction
does not waive any check for implementation, launcher, policy or skill changes.

After a completed change run a focused ordinary interactive Codex live scenario.
Use `./scripts/cortex-live-smoke start --workdir PATH` for the exact
`cortex-markdown-smoke` session on the default tmux server. The helper creates bash,
attaches an owner-only output pipe, and enters the launcher literally. Observe
`capture` and `status`; visibly confirm any trust prompt before the one explicit
`enter`, and confirm the composer before submitting work. Check the passive MCP
initialization receipt against the isolated candidate and seven-tool catalogue.
Submit with `send --prompt-file FILE`: one literal insertion, five real seconds,
then one named Enter. The transport never decides readiness or acceptance.
Workloads begin with the actual `$cortex:orchestrator` token; remaining text is
ordinary product work without orchestration test instructions.

Use `events` to inspect bounded metadata-only MCP outcomes for coordinator and
native workers, and `calls` to inspect every host-tool invocation and observed
result. A command wrapper must expose its exit code or running session receipt; stdout
alone is unverified. Never accept hidden errors, truncation or unexplained duplicate writes as a
clean run. When rejecting a live run, capture the complete current `calls` and
`events` tails and run `audit` before stopping it; do not stop monitoring after the
first discovered fault. The ordinary helper seals a receipt-bound immutable pre-stop
bundle before cleanup; any post-stop no-session diagnostic must use its distinct
post-stop path and must never replace the sealed calls or events evidence. The coordinator owns every user steering and acceptance decision.
For resume stop only that session, then start with the same workdir and
`--resume-last`; confirm the existing task before continuing. No `codex exec`,
nested tmux, alternate sockets or stable plugin environment. Capture the exit
marker and use `stop` (or `stop --interrupt` after failure) to remove the exact
session and temporary observation streams. A normal successful stop requires the
owned bash marker; missing-marker evidence remains incomplete and preserves the
session for an explicit failure path. Never kill the tmux server.

Real Desktop uses `scripts/cortex-desktop-dev`, the same isolated candidate and
a disposable Electron profile. Start with `--prompt-file`, confirm the prepared
composer, and use its `send` command, which focuses that exact window, submits with
one plain `Return`, and succeeds only after one new task receipt appears. Use `events` for MCP outcomes and `calls` for every
argument-free coordinator/worker host-tool invocation; review every call for necessity
and correct role ownership. CLI/Desktop parity requires consecutive successful real-host
runs on one unchanged payload. State an unavailable host as unverified.
