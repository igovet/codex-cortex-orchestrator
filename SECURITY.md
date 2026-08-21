# Security policy

## Supported versions

Security fixes are prepared for the current `9.2.20` source line. The public
contract is `cortex/orchestration/v5` and the durable ledger remains
SQLite `cortex/v8`. New tasks use pipeline contract v2. Existing active tasks
without that field are treated as v1 and resume their persisted pipeline; they
are not silently migrated or replayed.

The 9.2.20 source candidate retains stopped-report and duplicate-skill-read
recovery integrity, removes briefing size rejection while preserving immutable
artifact transport, and scopes no-progress pauses to their exact gate. Its
bounded report intake uses a prepare/commit CAS with retryable busy and stale
preparation outcomes; lifecycle hooks use a read-only SQLite snapshot and
fail-open telemetry writes. All mutation paths use bounded state-lock
acquisition; the acquired report commit remains serialized until its canonical
SQLite/artifact writes finish. `ledger_busy` holder metadata excludes the
private owner token.
Its exact source cachebuster is generated from
the 9.2.20 base version;
tracked-release and installed-plugin parity remain separate gates, and this
source-tree note is not a publication or installation claim.
The 9.2.16 source candidate retains stopped-report recovery integrity, private
report-draft descriptor validation, importlib-safe lifecycle-hook runtime
resolution, and active corrective-report preservation across multi-hop
handoffs. A later no-findings gate cannot silently resolve a server-created
verification blocker from another gate, and governance-origin rework carries
the fresh origin verifier through the later closure route. For every active
closure-rework route, dispatch preflight now requires a current server-bound
passed corrective receipt before the origin verifier is created; that avoids
an impossible PASS-resolution report contract without accepting unproven
provenance. Coordinator recovery no longer asks a reviewer to retry an
impossible resolution report; review and close preserve honest `BLOCKED`
markers while corrective work remains; and the `record_report` schema branch
matches runtime validation.

The public report boundary treats malformed JSON types as bounded validation
input: they receive a caller-correctable response without exposing Python
exception details, report content, or draft data through MCP errors.

Backup bundles contain a private copy of the governance lifecycle key required
to authenticate restored records. Treat each `.cortex-backup` directory as a
secret-bearing artifact: preserve its owner and mode, do not share it through
logs or public issue attachments, and use the verifier before accepting it as a
disaster-recovery source. The key is never returned by maintenance APIs.

## Reporting a vulnerability

Do not include credentials, personal data, private task reports, or exploit
details in a public issue. Use the repository host's private vulnerability
reporting feature when it is explicitly available for the published Cortex
repository. If no confidential reporting channel is shown, contact the
distributor privately through its verified platform identity before sharing
details.

A release must not claim a confidential email address or security contact that
has not been configured and verified. Catalog submission remains blocked until
reviewers can confirm a working private reporting route for the published
repository.

Include the affected version, impact, minimal reproduction, and suggested
remediation. Never attach a real token, key, user ledger, backup, or customer
export. Maintainers should acknowledge a report, coordinate a remediation and
disclosure timeline, and publish a new cachebusted version rather than mutate
an existing public tag.

## Security boundaries

Cortex writes its durable ledger below the host-private state root, which
defaults to `~/.codex/cortex/projects/p-<sha256>/`. The host-only
`CORTEX_HOST_STATE_DIR` override must be private and outside the workspace.
The release excludes runtime state, bytecode, symlinks, and secret-prone
paths. A legacy project-local `.codex/cortex` database is moved only by a
same-filesystem atomic rename after secure ancestry, database, and split-state
checks; unsafe, non-database, or cross-filesystem state fails closed. The
installer backs up only authenticated legacy targets and refuses unexpected
paths or symlink ancestry.

Worker prompts are immutable, read-only briefing artifacts addressed by an
exact path and SHA-256 digest. Native dispatch carries only the compact
identity/bootstrap needed to retrieve that briefing; workers cannot browse
other ledger artifacts except the exact report `draft_path` returned to that
worker. `get_report_template` creates this task- and attempt-scoped JSON file
with mode `0600`; the server stores only its exact path, identity, expiry, and
draft metadata in SQLite. Public tools accept the opaque `draft_ref`, not
an arbitrary caller path, and reject path escape, symlinks, non-regular or
non-owner files, every mode other than exact `0600`, identity mismatch, or
expiry. Draft reads recheck the opened descriptor rather than repairing an
untrusted path after lookup. `record_report` accepts only worker identity,
`draft_ref`, and report payload fields; coordinator `task_ref`, `dispatch_ref`,
and `submission_id` are rejected. Invalid `record_report` keeps the
same file for correction; successful `record_report` deletes it and its
metadata only after the durable report commit. A new template supersedes the
prior attempt draft.
Task-controlled prompt values are JSON-serialized into an explicitly untrusted
assignment block. They are never interpolated as headings, delimiters, or
protocol lines, so Markdown fences, role-like labels, XML-like tags, control
phrases, and multilingual text remain data rather than worker instructions.
Reports retain the strict seven-field
`cortex/report/v1` contract. The sensitive diagnostic log at
`~/.codex/logs/cortex-tool-errors.jsonl` is permission-protected and capped at
10 MiB by retaining complete newest records and dropping the oldest records
first. New records retain only value-free input shape metadata (source, byte
size, bounded top-level field names, and sensitive-field count), never tool
argument values, report bodies, question text, or user-authored content.
Secrets, credentials, personal data, and private report contents must
never be placed in prompts, reports, issues, or logs.

The governance bearer is returned only with the original successful start
response. Governance schema v12 persists only SHA-256 verifiers plus
server-owned claims: exact task/initiative scope, principal, thread, allowed
actions, generation, expiry, and revocation history. It also persists only a
verifier for a separate, non-durable coordinator recovery proof. It never
persists either reusable plaintext value or reissues them on an idempotent
retry. If a recovery response is lost, recovery is available through the normal
compatibility projection or an explicit `coordinator` MCP audience and requires
the exact same principal/thread/task plus the original recovery proof. It
stages a derived replacement pair, safely redelivers that exact pair while the
old proof remains valid, and commits rotation only when acknowledgement presents
the old proof together with both replacement values. The registry retains only
an opaque delivery reference and verifiers, never raw secrets. A lost initial
start response remains fail-closed: public identifiers are not a recovery
credential. An explicit `worker` audience cannot call either phase. It cannot
recover a bearer for another task or identity. Any legacy plaintext bearer or
proof is deleted and invalidated on first registry access; the affected task
fails closed instead of preserving a possibly compromised credential. Workers
must never receive or persist this coordinator-only bearer or recovery proof.

Schema v11 appends every governance record status and approval-basis
transition to an immutable, cryptographically linked lifecycle chain. Schema
v12 additionally authenticates the complete lifecycle event envelope with a
host-private key kept outside the SQLite ledger; missing or unavailable key
material fails closed. The
mutable current projection is accepted only when it matches that chain. A
pre-v10 upgrade deterministically reconciles safe v9 duplicate scope revisions
and sibling successors before v10 uniqueness constraints; missing scope links,
cross-scope predecessors, cycles, and other ambiguous graphs fail closed. A
governance-linked initiative task link cannot be deleted, and an initiative
cannot become completed or closed until every linked milestone/deliverable
task has ledger status `completed`.

`governance_mode=off` is fail-closed: C1 callers must submit an exhaustive
boolean assessment of all documented hard and topology triggers. Keyword
classification and positive structured evidence may only raise the governance
floor. Sensitive governance records require an approved exact-scope/type
policy with bounded `retention_days` and allowed actor roles; record expiry is
derived or constrained by that policy, optional allowed/redacted field rules
are enforced before persistence, and expired rows remain only as audit
history. Independent close evidence is bound to the canonical passed
`governance_close` code-reviewer attempt, its report reference, and a completed
native worker session rather than caller-authored reviewer labels.

Each worker-authored report is retained as a complete immutable artifact after
sensitive-key redaction; the former 64 KiB and 100-item sanitization caps do
not truncate submitted evidence. The explicit atomic artifact boundary is
8 MiB, while the private report draft envelope allows 17 MiB so its metadata
cannot reject a valid report. The separate per-attempt report-record limit
remains 32; there is no task-wide aggregate-byte quota. Reads are cursor-paged
over the complete artifact, and
32 KiB limits only an individual transport page. Detailed plans are passed to
workers by their exact immutable artifact ref/path/SHA-256 metadata in the
briefing.
Long-running histories grow in project-scoped SQLite subject to real storage
availability and explicit pruning. Successor prompts receive only the verified
transitive handoff frontier; covered history remains immutable and included in
Planner evidence digests. Each report automatically carries the complete
task-wide canonical `resolved_user_decisions` snapshot; replacement briefings
carry a bounded recent projection so answered intent survives attempt changes.

Worker-facing caller, input, and schema validation failures are structured as
same-attempt corrections and do not consume recovery budget. Evidence-backed
pipeline rework has no fixed attempt or same-strategy quota while acceptance,
verification, or canonical findings remain unresolved. A materially identical
no-progress signature is a separate liveness boundary: autonomous retries
pause for an explicit new strategy, preserving the failed gate and evidence
instead of synthesizing a pass. Recovery must begin with a singleton Planner
wave and materially change the failed pipeline, strategy, or verification
contract; infrastructure/environment pauses may instead name a class-matched
remediation in that Planner wave. Free-text completion/resume reason prose is
audit-only and cannot release the pause. Repeated failures automatically raise
reasoning effort and, for eligible workers, escalate routing to Terra instead
of silently closing the task.
Only explicit non-retryable integrity, storage, permission, or unavailable
identity failures terminate that worker attempt. Bounded briefing and
coordinator artifact reads clamp oversized `max_bytes` requests to 32768;
report reads use the same transport-page bound while returning the complete
immutable artifact through cursors.

Manifest capture is fail-closed and bounded by maximum entries, hashed bytes,
and elapsed time. Budget exhaustion returns a partial result with a reason and
cannot authorize read-only mutation reconciliation, a complete handoff, or
terminal close. A bounded stat-keyed digest cache is an optimization only.
The release workflow requires the 50,000-file benchmark to report
`target_met: true`; benchmark output is temporary and must not contain
credentials or enter the release archive.

Required plan approval is bound to a specific plan revision, planner report,
verified predecessor evidence digest, and semantic future-pipeline digest. A
material replan preserves the prior approval in history and requires a new
approval; no-op or transport-only changes do not invalidate it. A mismatch
blocks dispatch with recoverable reapproval guidance.

Questions shown to users by the root coordinator must be localized to the
user's original language. Cortex returns a bounded `cortex/chat-interaction/v1`
projection; the root renders it as one detailed ordinary final assistant
message and ends the turn. It must not call a UI, input, approval, or
elicitation tool. The user's next ordinary message is recorded against the same
durable interaction before the exact worker resumes. Worker protocol messages
and durable worker reports remain English.
Workers return `QUESTION_RECORDED` with a complete handoff containing the
decision context, full self-contained questions, concrete outcome-based options,
descriptions, trade-offs, and recommendation. Every question must name the
LLM's recommended option IDs or recommended free-text answer and explain the
rationale. Generic numbered, A/B, or
recommended/alternative placeholders are rejected; descriptions may be shown
with rendered options. Localized chat projection text must not alter canonical question or
answer values. Batch projections accept the documented `localized_question`,
`localized_header`, `localized_options`, and `localized_custom_label` fields,
plus the compatibility aliases `question`, `header`, `options`, and
`custom_label`. Choice questions always permit optional free-form constraints
in addition to server-owned options. Localized custom text is preserved in its
original form and withheld from workers until a canonical English translation
is recorded. Successors may reopen a resolved decision only after an explicit
current user change.

Question records also bind task revision, plan revision, and attempt strategy
generation. A material steer supersedes unresolved questions and downstream
evidence; a stale answer cannot be applied to a newer revision. Quotas are
scoped to the active revision/generation rather than becoming a permanent
cross-revision denial of a required blocking question.

The archive boundary is validated from `git archive HEAD`, not the mutable
working tree. A repository with an unborn `HEAD` has no release archive to
validate: `--require-tracked` must remain a publication blocker until an
authorized initial commit exists and the check passes against it.

This repository is a source candidate only. The prior
9.2.4+codex.20260819182839 build was not installed from this checkout and is
not published or tagged here. The current source cachebuster is
the current source manifest; installation and publication remain pending.
