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

For an active Cortex task, preserve `task_ref` as the mandatory ledger-recovery
anchor, plus the absolute project root, governance mode, applicable knowledge
paths and constraints, exact task/result contract, current plan/report digest,
and known initiative, delegation, report, and user-decision IDs. Preserve each
decision's subject type, subject ID, subject digest, supersession, and effect on
the orchestration DAG revision. Preserve incomplete report assembly state: report ID, state,
next chunk index, expected count/digest when known, and completed section
manifest. Preserve verified plan/report projection paths with their source
sequence or revision and digest, but treat them as publishable only after fresh
reverification. Task, decision, delegation, initiative, closure, governance,
handoff, index, and timeline records have no Markdown paths. These references
are not bearer capabilities.

Preserve every ID, digest, and cursor byte-for-byte as opaque immutable return
data. Never recover one by parsing, concatenating, normalizing, reconstructing,
or appending a remembered suffix; copy it from a successful retained response
or fresh inspection.

Also retain known live native child handles, task names, mutation scopes, and
last observed host status as bounded private host context. They are not ledger
rows, must not enter durable reports or user-facing links, and do not prove
that a worker is still running. Preserve an unconsumed returned native-dispatch
payload byte-for-byte with its exact delegation association and whether its one
allowed host spawn was attempted; never reconstruct or redispatch it from
memory. After compaction:

1. Use `inspect_task` with the known preserved `task_ref` to recover the task
   header and chronological frontier.
2. Use `inspect_governance` when mode history, initiative links, dependencies,
   or closure history affects the next decision.
3. Use `read_delegation` or `read_reports` only for the specific details,
   selected sections, and evidence needed next. Resume paginated reads with the
   returned cursor. Resume an assembling report from its recorded
   `next_chunk_index`; never restart or finalize from a guessed count/digest.
4. Reverify any projection's containment, freshness, and digest before
   publishing it. A stored path alone is never sufficient.
5. Recover the current orchestration DAG from the exact task contract, persisted
   initiative revision, immutable planner plan revision/digest, decisions, and
   report evidence; then continue safe work. Never reconstruct or author the
   project solution plan—the planner report remains its sole authority.

Only `create_task` receives `project_root`. Recovery calls use the preserved
`task_ref`; do not resend the root as an MCP argument. Keep the root only for
native worker working-directory context and a self-contained fallback brief.

For the coordinator, recovery remains coordination-only: do not reopen target
project files, search the repository, rerun commands, or reconstruct technical
facts through direct analysis. Delegate any missing project discovery,
implementation, or project verification and recover its evidence through a
report ID.

Recovery never permits root discovery or project-local artifact/state probes.
Git, manifests, caches, worktrees, existence/absence or unchanged-state, and
project-local `.codex` must be checked by a worker and returned as evidence.

Do not promise ID-less enumeration. If `task_ref` was not preserved, do not
claim that the ledger can rediscover the task from project root, filesystem,
session, or other task content. Continue from safely preserved native evidence
and self-contained worker briefs when possible, or disclose the ledger context
loss. Never invent an ID or create a duplicate task merely to simulate
recovery.

If a native child was known live before compaction, reconcile that exact handle
through ordinary host coordination. Continue or follow it up only when the
handle and ownership scope remain known and safe. When its state is unknown,
do not overlap mutation ownership; wait for bounded host evidence when useful,
or create a `parent_delegation_id`-linked replacement after containing the
scope. A durable delegation with no report does not reveal whether the native
child never started, is running, stopped, or was abandoned.

If the ledger, selected report read, or projection service is unavailable,
retain sanitized native evidence and continue through a complete self-contained
worker brief when safe. Disclose the durability or human-view limitation and
never invent recovered rows, report content, decisions, or links.

Missing closure, partial or unavailable reports, unfinished linked work, and
unresolved dependencies remain advisory context; none prevents native
delegation or an honest final answer.
