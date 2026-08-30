---
name: context-compaction
description: Internal Cortex v12 recovery overlay. Load only for an explicitly activated Cortex task after compaction, reset, or a required bounded handoff.
---

# Durable Context Recovery

Do not preserve raw transcripts by default. A bounded handoff should retain the
current outcome and acceptance criteria, verified project facts and source
references, material decisions, changed files, decisive checks, open user
questions, and the next evidence-backed action. Never include secrets,
credentials, personal data, private logs, or raw tool streams.

For an active Cortex task, preserve the mandatory server-issued task anchor for
ledger recovery, plus the absolute project root, governance mode, applicable
knowledge paths and constraints, exact task/result contract, current
plan/publication digest, and known initiative, assignment, evidence, and
decision relations. Preserve each decision's subject, digest, supersession, and
effect on the orchestration DAG revision. Preserve incomplete publication
assembly state, continuation state, expected digest when known, and completed
section manifest. Preserve verified plan/publication projection paths with
their source revision and digest, but treat them as publishable only after
fresh reverification. Task, decision, assignment, initiative, closure,
governance, handoff, index, and timeline records have no Markdown paths. These
references are not bearer capabilities.

Preserve every server-returned identifier, digest, and continuation token
byte-for-byte as opaque immutable return data. Never recover one by parsing,
concatenating, normalizing, reconstructing,
or appending a remembered suffix; copy it from a successful retained response
or fresh inspection.

Also retain known live native child handles, task names, mutation scopes, and
last observed host status as bounded private host context. They are not ledger
rows, must not enter durable reports or user-facing links, and do not prove
that a worker is still running. Preserve an unconsumed returned native-dispatch
payload byte-for-byte with its exact delegation association and whether its one
allowed host spawn was attempted; never reconstruct or redispatch it from
memory. After compaction:

1. Use `read_task` with the known preserved task anchor to recover the task
   header and chronological frontier.
2. Use `assess_governance` when mode history, initiative links, dependencies,
   or closure history affects the next decision.
3. Use `read_task` and `consume_assignment_evidence` only for the specific
   details and evidence needed next. Resume bounded reads with the returned
   continuation state. Resume an assembling publication from its recorded
   continuation state; never restart or finalize from a guessed digest.
4. Reverify any projection's containment, freshness, and digest before
   publishing it. A stored path alone is never sufficient.
5. Recover the current orchestration DAG from the exact task contract, persisted
   initiative revision, immutable planner plan revision/digest, decisions, and
   report evidence; then continue safe work. Never reconstruct or author the
   project solution plan—the planner report remains its sole authority.

Only the initial task-opening operation receives the project root. Recovery
calls use the preserved task anchor; do not resend the root as a tool argument. Keep the root only for
native worker working-directory context and a self-contained fallback brief.

For the coordinator, recovery remains coordination-only: do not reopen target
project files, search the repository, rerun commands, or reconstruct technical
facts through direct analysis. Delegate any missing project discovery,
implementation, or project verification and recover its evidence through a
report ID.

Recovery never permits root discovery or project-local artifact/state probes.
Git, manifests, caches, worktrees, existence/absence or unchanged-state, and
project-local `.codex` must be checked by a worker and returned as evidence.

Do not promise identifier-less enumeration. If the task anchor was not
preserved, do not
claim that the ledger can rediscover the task from project root, filesystem,
session, or other task content. Continue from safely preserved native evidence
and self-contained worker briefs when possible, or disclose the ledger context
loss. Never invent an ID or create a duplicate task merely to simulate
recovery.

If a native child was known live before compaction, reconcile that exact host
handle
through ordinary host coordination. Continue or follow it up only when the
handle and ownership scope remain known and safe. When its state is unknown,
do not overlap mutation ownership; wait for bounded host evidence when useful,
or create a parent-linked replacement after containing the
scope. A durable delegation with no report does not reveal whether the native
child never started, is running, stopped, or was abandoned.

If the ledger, selected report read, or projection service is unavailable,
retain sanitized native evidence and continue through a complete self-contained
worker brief when safe. Disclose the durability or human-view limitation and
never invent recovered rows, report content, decisions, or links.

Missing closure, partial or unavailable reports, unfinished linked work, and
unresolved dependencies remain advisory context; none prevents native
delegation or an honest final answer.
