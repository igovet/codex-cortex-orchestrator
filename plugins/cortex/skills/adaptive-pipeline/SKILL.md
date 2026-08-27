---
name: adaptive-pipeline
description: Internal Cortex v12 adaptation overlay. Load only for an explicitly activated Cortex task when new evidence changes delegation, verification, or governance depth.
---

# Evidence-Driven Adaptation

Adapt the model-owned orchestration DAG from evidence, not from a backend state
machine. This overlay is operational: load it after explicit Cortex activation
whenever a worker report, user decision, failed/incomplete check, changed risk,
contradiction, scope change, or documentation-impact finding could change later
work. The coordinator changes pipeline nodes only; it never writes the project
solution plan. When planning is required, a `planner` worker must publish the
durable `plan` report that plan-dependent nodes name as their predecessor.

## Dynamic pipeline state

Maintain an evidence-backed DAG and persist its current projection through the
existing task-linked initiative revision plus delegation/report/decision links.
For each stage retain: its unique purpose; worker owner/profile/model/effort;
scope and mutation boundary; named predecessor report/decision refs; acceptance
and expected evidence; and state. Valid model-owned
states are `proposed → waiting_predecessors → ready → dispatched →
evidence_received → settled`, with `rework_required` returning to a new
parent-linked `proposed` node and `cancelled` permitted only for unstarted work
made unnecessary by evidence or a user decision. These labels are coordinator
reasoning persisted as advisory pipeline revisions, not backend lifecycle states
or tool admission rules.

Use this transition discipline:

1. Read the bounded relevant report sections or decision; record which evidence
   changes which assumption, risk, acceptance criterion, or predecessor.
2. Decide whether current nodes still cover the outcome. Add, remove, reorder,
   retry, or create rework only for the evidence-backed delta, then append the
updated pipeline revision with its stage owners, input report/decision refs,
   acceptance, evidence, and rationale. Completed worker reports remain
   immutable and are never relabeled as a new solution.
3. Preserve parallelism only for non-overlapping ownership. Make a predecessor
   edge only when a later worker needs its evidence, particularly the finalized
   planner plan report for plan-dependent implementation or review.
4. Dispatch the next ready worker through a new durable delegation. A retry or
   rework uses the exact emitted `parent_delegation_ref`; do not reuse a worker or invent a report.
5. Reassess C1/C2/C3, governance depth, residual risk, verification depth, and
   the final documentation branch after every material evidence arrival.

After each revision, update the standard Codex To-Do projection from the latest
DAG. To-Do contains only current pipeline stages and gate state, never worker subtasks,
implementation checklists, or report bodies. Keep it concise and current with
the persisted initiative revision; update it when a stage or gate changes, and
when evidence adds, removes, reorders, retries, or reworks a node. The To-Do
projection is for live progress only and is not a second durable task ledger.

The C-level is advisory: C1 normally starts minimal, C2 light, and C3 full.
Escalate or reduce the advisory starting-depth label only with recorded
evidence; an explicit user choice remains the governing preference subject to
ordinary live safety gates. A reassessment cannot weaken an already-required
plan/approval obligation: once the current task has a persisted light/full gate
or the user explicitly requires planning and approval, a model cannot bypass it
by selecting C1/minimal after a planner or report failure. No C-level or state
label creates a backend wave, mandatory stage, model escalation, or new
user-approval gate.

The durable light/full governance gate is narrower: it validates the planner
report/approval decision relation before downstream delegation. Its required
relations are monotonic for the task: preserve the exact opaque plan report ID,
plan digest, and approval decision ID in each successor input list. A failed,
partial, missing, or schema-rejected planner report requires correction, rework,
or a parent-linked planner replacement—not a downgrade, native-prose
substitute, explorer/QA/implementation bypass, or free-text instruction. After
the replacement submits a finalized completed plan, obtain one new explicit
localized user decision. If it is `approve`, read the plan to obtain the exact
ready `approval_view`, copy its server-issued approval handle and exact
report/view digest and sequence, and record approval against those exact values
before dispatching downstream work. A `request_revision` or `cancel` decision
is recorded against the exact finalized plan digest and response without a
volatile view binding. Never reuse the original task request or infer consent.

The coordinator makes adaptation decisions only from user input, ledger state,
and worker reports. Any new project inspection, domain analysis, implementation,
command, or verification required to resolve uncertainty must be delegated; the
coordinator never performs that work itself.

The coordinator can select discovery, planner, implementation, review,
verification, security, documentation-impact, documentation-sync,
documentation-verification, knowledge-harvest, or closure-evidence stages only
when governance, user constraints, or worker evidence justifies them. Their
order and presence are dynamic; documentation is a final conditional stage,
while knowledge harvest is selected only by an explicit harvest route.

Reassessment may compare reported evidence with acceptance criteria, but it
must not turn into direct inspection or testing of the target project. Create a
focused evidence-gathering or verification delegation when the reports do not
support a decision.

Do not use a report gap to inspect Git, manifests, caches, worktrees, file
existence/absence or unchanged-state, or project-local `.codex`. These are
project-state checks and always require a focused worker delegation, even when
the user asks the coordinator to perform one directly.

When evidence expands or moves the project scope, the coordinator updates its
bounded route and compiles a revised delegation knowledge contract for the next
worker. A missing or unreadable index creates bounded discovery; a reported
stale or conflicting document is handled according to task impact. Neither
condition permits coordinator source inspection or automatically forces
harvest.

If rework or new evidence changes behavior, architecture, interfaces, commands,
verification, conventions, feature ownership, or public usage after an earlier
documentation assessment, reassess documentation impact from the new worker
reports. Create or revise a documentation-sync worker delegation when needed,
and ensure a separate worker verifies documentation when the impact is
material. Otherwise retain the updated `documentation not required` rationale.
For light/full governance, perform this reassessment before advisory closure:
the closure relation requires a post-approval technical_writer report that
names the approved plan/decision and relevant finalized result refs, followed
by a coordinator report read. The report content remains opaque and no-impact
does not require a documentation edit.

Use `set_governance_mode` for an evidence-backed mode revision. Use
`create_delegation` for new, replacement, or rework ownership. The model
chooses the role, exact model, and reasoning effort for every delegation; the
backend records the choice but never promotes a model, substitutes a profile,
or chooses a recovery route.

Routing remains Luna-first: Explorer and ordinary discovery always use Luna,
with effort increased before a model change. Terra requires evidence of
genuinely complex non-security work or planning; Sol is reserved for security
work and review. Do not encode an automatic escalation ladder in a pipeline
revision.

A failed worker, partial report, missing report, or `not_ready` closure is
evidence for the model's next decision, not a lifecycle barrier. Create a
replacement when it is useful, proceed with other safe work, ask the user when
their decision materially changes intent, or finish with a clear limitation.
This flexibility never overrides a required plan/approval hold: when the failed
worker is its planner or its report is missing/rejected, safe work is limited to
planner recovery and already-authorized planning/discovery; no downstream
project stage may start until finalized plan and explicit decision evidence
exist.

Before replacing a reportless worker, wait no more than 60 seconds per call;
after the first quiet interval send the exact `native_task_name` an English
checkpoint request, then inspect/list status after later intervals. If the
worker remains running, keep waiting and update the user; silence never proves
it is stuck. Interrupt and same-handle follow up only for explicit
failed/unavailable/idle-without-work evidence, host-confirmed no-progress, or
user cancellation. If the child is live and its mutation scope is active, avoid
overlapping write ownership. If its state is unknown, preserve uncertainty and
do not infer that a durable delegation proves it started or stopped. Only failed
or ambiguous authorized recovery permits blocked evidence and a replacement.
Every semantic replacement or rework delegation uses the exact emitted
`parent_delegation_ref` and carries only relevant input report and user-decision
refs. Do not create a new task, skip a planner predecessor, or silently start a
downstream stage to simulate recovery. C-level/timebox can shorten the count,
never alter server-owned model routing.

A user question may concern task requirements, scope, acceptance or product
behavior, or explicit external/destructive authorization. Never turn internal
ledger status, mode, dependency, closure, retry, or worker conditions into a
user decision.

Pipeline progress is continuous until the requested outcome and closure
evidence are complete. A worker completion, incomplete or waiting stage,
technical/documentation/review error, Demo or production gate, or quiet host
interval is evidence for the next safe coordinator action, not permission to
end the task. Reconcile and dispatch, follow up, replace, or recover the next
safe node automatically. The coordinator may pause before closure only after
asking one genuine user question that materially changes requirements, scope,
acceptance, or required external/destructive authority; after the answer,
resume from that decision. Never leave an unfinished DAG stopped without such
a question and an explicit recorded reason.

When a genuine decision is required, ask one complete question in the language
of the latest meaningful user message and end the turn. Silence is not an
answer. Record the exact response and separate English normalization against
the immutable subject/digest before using it. Follow up the same native child
only when its live handle and ownership are known and ordinary host follow-up
is safe; otherwise create a parent-linked replacement. Cortex does not
guarantee same-child continuation.

For plan review, `approve` applies only to the exact plan report and digest and
requires the current ready approval view plus opaque approval handle. Plan
`request_revision` and `cancel` preserve the exact finalized plan digest and
response without volatile view binding, so intervening non-plan timeline events
cannot block feedback. Revision records the user's feedback verbatim, preserves
the old plan, creates
a new immutable plan/digest through the same live planner when possible (or a
parent-linked replacement only when it is unavailable), then asks for a new
decision. A requested/necessary main plan must have a finalized verified
Markdown link and explicit localized approval. Record `approve` against its
exact digest plus the ready-view binding and pass that decision ID to every
plan-dependent delegation; do not
dispatch implementation or research beyond discovery/planning first. Cancellation
stops later dispatch by coordinator policy, not backend gate. If persistence is
unavailable, never infer approval; proceed only from an unambiguous safe user
message and disclose the non-durable decision.
