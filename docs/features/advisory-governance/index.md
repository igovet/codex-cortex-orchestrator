# Advisory governance and project initiatives

<!-- GENERATED:START -->

## Purpose

Cortex V12 records model-owned governance reasoning without turning it into a
backend workflow or permission system. Assessments, initiative revisions,
links, warnings, and closures create a durable audit trail while the model owns
every orchestration and safe next-step decision. The root coordinator remains
an orchestration-only control plane; workers own all project actions,
source/project-grounded access, substantive domain analysis, implementation,
and verification. Its only project-read exception is the bounded
orchestrator-owned knowledge route used to compile delegation requirements.

## Key files

- [public_contracts.py](../../../plugins/cortex/scripts/cortex_runtime/public_contracts.py) defines the governance and decision tools, including the three narrow decision operations, in the uniform fourteen-tool semantic catalog.
- [v12_service.py](../../../plugins/cortex/scripts/cortex_runtime/v12_service.py) exposes action-specific governance operations.
- [v12_store.py](../../../plugins/cortex/scripts/cortex_runtime/v12_store.py) owns append-only assessments, initiative revisions/links, warnings, and closures.
- [orchestrator/SKILL.md](../../../plugins/cortex/skills/orchestrator/SKILL.md) defines outcome-first model behavior.
- [cortex-control/SKILL.md](../../../plugins/cortex/skills/cortex-control/SKILL.md) summarizes the fourteen public operations and nonblocking invariant.

## Mode assessments and revisions

The coordinator records one of three modes after creating a task:

| Mode | Selection guidance | Expected depth |
| --- | --- | --- |
| `minimal` | Default for bounded, low-risk, single-scope work | Outcome, acceptance, delegated proportional verification, unresolved items |
| `light` | Multi-step/cross-component work, user-visible behavior, ambiguous acceptance, or substantial code change | Decisions, assumptions, risks, delegated verification plan, closure reflection |
| `full` | Security, privacy, authentication, financial, destructive, production-critical, multi-repository, multi-task, long-lived initiative, or explicitly requested work | Risk register, delegated falsification and independent-review evidence, detailed closure |

After `open_task` and before the first assignment, the coordinator must record
one evidence-backed initial assessment. `assess_governance` appends an assessment with source `model` or
`user_override`. An explicit user override has priority and is stored unchanged;
the backend does not classify, promote, downgrade, or reject it. The model may
record a concise risk warning.

When new evidence changes the appropriate depth, the model appends a new
assessment and explains both the evidence and the revision rationale. There is
no stale-revision check. With no user override, the latest model assessment is
effective. Once present, the latest user override remains effective across
later model assessments; those rows retain evidence, warnings, and revised
recommendations without silently replacing the user's choice. Earlier
assessments remain audit history.

Governance depth never expands the coordinator's project permissions. Even in
`minimal` mode, verification is delegated; even in `full` mode, falsification,
security review, and independent review are worker-owned. The coordinator
reasons from user input, bounded knowledge-routing reads, ledger records, and
worker reports. Profiles consume the compiled knowledge contract and never
independently redo routing.

C1/C2/C3 remain model-owned starting baselines rather than static pipelines:
C1 is bounded low-risk work and normally `minimal`; C2 is multi-step or
cross-surface work and normally `light`; C3 is high-risk or cross-domain work
and normally `full`. Evidence and direct user preference can revise either
classification or mode. Neither label creates a backend wave, mandatory stage,
model escalation, or user-approval gate.

Light/full mode selection remains model-owned, but every delivery assignment in
those modes requires a current finalized plan with `review_policy=required` and
an explicit approval bound to that exact report and digest. The backend enforces
this narrow pre-dispatch integrity relation while leaving planning/evidence
assignments available; it does not choose a schedule. A minimal plan may use
`review_policy=informational` only when
no material product, scope, external, destructive, security, privacy, or risk
decision remains. The selected policy is persisted on the immutable plan report.

## Project-level initiatives

An initiative is a durable project object for several tasks sharing a
long-lived goal, common risk, milestone/dependency relationship, or common final
assessment. It may:

- outlive an individual orchestration task;
- link several tasks and immutable reports in the same project ledger;
- reference a parent initiative;
- depend on other initiatives;
- retain model-authored goal, risk, notes, and informational status;
- receive its own closure after the broader program can be assessed.

The private/internal ledger helper `record_initiative` creates a stable initiative or appends a revision. Status
is limited to `proposed`, `active`, `paused`, `completed`, `closed`, and
`cancelled`, but the backend accepts every transition among these values. It
does not assign an owner, select a milestone, compute completion, or enforce a
transition graph. Its required `task_ref` only anchors the saved project ledger;
the returned durable `task_id` is non-callable evidence, and the task does not
grant initiative permission or lifecycle authority.

For one task, the coordinator also uses the task-linked initiative revision as
the durable projection of its model-owned orchestration DAG. A revision records
the evidence-backed stages derived from the planner report (or the minimal C1
task contract), each worker owner, predecessor report/decision refs, acceptance
and expected evidence, and the current advisory state. The coordinator never
writes the project solution plan: that is the planner worker's immutable `plan`
report. New evidence can append a revision that adds, removes, reorders, retries,
or parent-links rework stages. The backend stores this graph history but never
executes it or parses free text as workflow control.

The coordinator treats every returned compact task/entity reference, durable
evidence ID, and digest as opaque byte-for-byte data. Compact refs are the only
callable public locators; durable `*_id` values are non-callable evidence. It
never parses an embedded shard, reconstructs a value, normalizes it, or appends
a suffix before another call.

Task and report links must resolve in the current project. An unresolved
dependency identifier is retained with `unresolved_dependency`; a dependency
cycle is retained with `cyclic_dependency`. Warnings are model-visible evidence,
not rejection reasons. A later status update or initiative closure remains
allowed.

The `read_task` evidence view uses the task only to anchor the project and
returns initiatives/links related to that task, the effective mode projection,
immutable initiative revision payloads, and a bounded timeline page. An
optional compact `initiative_ref` narrows the view; the durable `initiative_id`
is evidence only and does not authorize or gate the initiative.

## Plans and user decisions

A plan is a finalized `report_type=plan` report. Its review policy is
`informational` or `required`; an updated plan names a finalized predecessor
through the plan-revision relation and receives a new immutable report ref and
content-manifest digest. Informational plans do not pause work. Required review
creates a coordinator-owned pause only for plan-dependent work when explicit
review or a real product, scope, destructive, external, security, privacy, or
risk decision is needed. It never changes public-tool availability.

The matching narrow decision record operation appends `user_via_coordinator` evidence for an exact
task, plan, initiative, delegation, or report through one closed canonical
request containing the task and subject refs, decision type, neutral `prompt`,
exact arbitrary-Unicode `response_original`, and asserted user language; include
`subject_digest` only for plan and report subjects. Retired `prompt_en` and
`response_en` fields are rejected. Decision
types are `approve`, `reject`, `request_revision`,
`clarification`, `cancel`, `accept_risk`, `override`, and `steer`. Plan/report decisions
require the canonical `sha256:<64-lowercase-hex>` subject digest; a plan must
already be finalized and completed. Only plan `approve` additionally requires a
current ready `approval_view` with its exact opaque handle, view digest, and
source sequence. A ready plan read also returns `handles.decision_binding` with
the existing decision-input names for direct field-for-field composition. Plan
`request_revision` and `cancel` preserve the exact
finalized plan digest and response without volatile view binding, so
intervening non-plan events cannot block feedback. Missing or cross-mixed fields
fail before mutation.
Clarification is not approval, and approval of an old digest never transfers to
a revised plan.

This is append-only coordinator-attributed evidence, not cryptographic human
attestation or a backend permission. The coordinator presents any required
review and verified current plan links in the user's language, then continues,
revises, or discloses risk according to the decision and worker evidence.

When the user requests a main plan, or planning is otherwise necessary, the
coordinator must present the finalized plan through a fresh verified host-private
Markdown link and request explicit approve/revise/reject/cancel input. Only an
`approve` response requires the current ready approval view and opaque handle;
`request_revision` and `cancel` retain the exact plan report/digest and response
without volatile view binding. When the work is plan-dependent, implementation
or research may use that compact decision ref in a plan-dependent delegation's
`input_decision_refs` array; this is coordinator-owned predecessor evidence, not
a backend admission rule. Rejection/revision
follows up the same live planner with that decision ref for a superseding plan,
or uses a parent-linked replacement
only when same-worker continuation is unavailable. A C1 task skips planning only
when the user did not request one and an explicit rationale is recorded.

## Advisory closure

`close_task` appends a model-authored task statement. Its public shape is
task-anchored: it accepts the exact `task_ref`, one `verdict`, and bounded
opaque JSON `evidence` plus optional risk/follow-up/completion notes; Cortex
validates evidence only as finite JSON within its size bound. The verdict is one of:

- `ready`;
- `ready_with_risks`;
- `not_ready`.

The exact anchored `task_ref` is the only public closure locator. Durable
`task_id` values are evidence only and are not callable closure inputs.
`unresolved_risks` and `follow_ups` are optional and default to empty lists.
The closure call has no subject or digest argument. Required reports must be
finalized and read before a ready closure is attempted.

The verdict is a recommendation. A task closure can cite acceptance and
verification evidence, unresolved risks, and follow-ups. A long-lived
initiative closure can aggregate task/report evidence, achieved goals,
unfinished milestones, accepted exceptions, residual risk, retrospective, and
recommended future work.

The coordinator chooses the verdict from sufficient completed worker-report
evidence after the documentation-impact stage. It then automatically attempts
the advisory write and inspects the intended task or initiative record. This is
an evidence-and-inspection convention owned by the coordinator; the backend
does not infer completion from a closure, require a closure before a final
answer, or make a closure a lifecycle gate. In particular,
`ready_with_risks` is a coordinator verdict and never requires user
confirmation. User confirmation belongs only to an applicable ordinary-chat
product or plan-review decision.

The public result keeps execution and bookkeeping separate. `read_task`
returns:

- `execution_outcome`, with fields `evidence_status`,
  `finalized_report_count`, `completed_report_count`, `effective_revision`,
  `coverage_status`, and `outcome`. Its outcome derives deterministically from
  current effective-contract coverage, independent of report arrival order and
  historical/superseded claims, and makes no native-lifecycle claim. The
  projection remains independent of the closure record;
- `advisory_closure`, with `record_status` (`recorded` or `not_recorded`) and
  `latest_record` (the latest advisory closure object or `null`).

`close_task` automatically follows its write with bounded
inspection and returns `closure_confirmation`. Its `inspection_status` is
`confirmed` when the intended record is observed, or `unconfirmed` with one of
`record_inspected`, `persistence_unavailable`, `inspection_unavailable`, or
`record_not_observed` as `reason`; `attempts` is always 1 or 2. Cortex makes at
most one same-idempotency retry for a verified transient persistence or
inspection failure. If the write or inspection remains unavailable, the
coordinator reports the unconfirmed advisory bookkeeping and retains the
independent neutral execution evidence; the advisory limitation does not alter
the closure verdict.

After `not_ready`, the model may create rework, delegate a specialist, request a
real user decision, or provide a final answer that clearly discloses the
limitation. The backend never chooses that route. An initiative may close with
unfinished linked tasks or unresolved dependencies when those facts are
recorded as residual risk.

After worker-reported project verification and before closure, the coordinator
makes a report-grounded documentation-impact decision. Material behavior,
architecture, interface, command, verification, convention, or
feature-ownership changes require a documentation-sync worker plus a separate
documentation-verifier worker. A no-impact task uses one finalized worker-owned
report with an explicit English documentation-impact section and
material/no-impact rationale and creates no empty edit. When existing finalized
reports do not already contain that section, a bounded English
evidence-synthesis/documentation-impact worker submits a finalized synthesis;
  the coordinator never calls a worker-only `publish_*` operation or self-asserts
`documentation_not_required`.

The no-impact close has a deterministic evidence sequence. After every required
publication is finalized, the coordinator creates or updates an initiative with
the `linked_task_refs` and `linked_report_refs` arrays, containing compact
`task_ref` and `report_ref` values, respectively, plus every other implementation
and verification report link. Closure evidence cites those
exact report references and returned digests. A report-only final initiative or
self-asserted no-impact value is invalid. The coordinator closes that exact
initiative, then uses the bounded `read_task` evidence view
first in task scope and then in initiative scope. Both `read_task` evidence views must surface the
task relationship, required report links, and closure before the coordinator
claims a durable `ready` verdict. A failed write or inspection is disclosed as
an advisory limitation and does not block an honest final answer. Missing
documentation evidence can trigger rework, replacement, or risk disclosure; it
never becomes a backend lifecycle gate.

## Absolute nonblocking guarantee

No governance record, mode, initiative status, dependency warning, linked-task
state, report/plan status, user decision, review state, closure verdict, missing
decision, missing closure, or human-view state may:

- prohibit `open_assignment`;
- prohibit `read_task`;
- prohibit any worker-only `publish_*` operation;
- require a wave, gate, worker receipt, host stop, or server recovery route;
- prevent evidence synthesis, model-owned rework, or a replacement worker;
- prevent a safe user-facing final answer.

Hard rejection remains limited to public-schema validation, strict JSON and
size constraints, reference existence, project isolation, SQLite integrity,
idempotency conflicts, and external Codex/user approval boundaries. If a
governance write is unavailable, the model continues safe work when possible
and discloses only material missing evidence.

## Verification

The release/protocol test must prove append-only mode history, preservation of a
user override, free initiative status transitions, unresolved/cyclic warning
retention, initiative closure with residual dependency risk, rework after
`not_ready`, plan/report digest binding, decision append-only behavior, and
final-answer availability without closure or a current view. It must also prove
that finalized worker-publication evidence yields the same independent
`execution_outcome` even without an advisory record, that `read_task` exposes the separate
`advisory_closure` projection, that closure automatically performs intended
inspection, and that one transient persistence/inspection retry yields a
bounded `closure_confirmation` without changing execution evidence. The
`ready_with_risks` path must complete without a user-confirmation request. The self-contained
skill/profile lint checks the structural governance, coordinator-only, textual
scope, language, plan-review, opaque-identity, worker-only report ownership,
skill-resource, evidence-ordering, final-initiative-link, and
conditional-documentation contract. Mutation fixtures reject ID construction,
coordinator report submission, premature/task-subject closure, report-only
final initiatives, MCP reads of `skill://` URIs, ad-hoc/cardinality-mismatched
native dispatch, localized child transcripts, self-asserted no-impact closure,
and free-form role text treated as profile proof. Actual
reasoning quality for `minimal`, `light`, and `full` requires interactive
Luna/high model evidence.

See [verification.md](../../project/verification.md), the
[orchestration ledger](../orchestration-ledger/index.md),
[human-readable task views](../human-readable-task-views/index.md), and
[release readiness](../../release-readiness.md).

<!-- GENERATED:END -->
