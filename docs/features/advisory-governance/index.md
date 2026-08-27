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

- [public_contracts.py](../../../plugins/cortex/scripts/cortex_runtime/public_contracts.py) defines the four governance tools, `record_user_decision`, and the uniform eleven-tool catalog.
- [v12_service.py](../../../plugins/cortex/scripts/cortex_runtime/v12_service.py) exposes action-specific governance operations.
- [v12_store.py](../../../plugins/cortex/scripts/cortex_runtime/v12_store.py) owns append-only assessments, initiative revisions/links, warnings, and closures.
- [orchestrator/SKILL.md](../../../plugins/cortex/skills/orchestrator/SKILL.md) defines outcome-first model behavior.
- [cortex-control/SKILL.md](../../../plugins/cortex/skills/cortex-control/SKILL.md) summarizes the eleven public operations and nonblocking invariant.

## Mode assessments and revisions

The coordinator records one of three modes after creating a task:

| Mode | Selection guidance | Expected depth |
| --- | --- | --- |
| `minimal` | Default for bounded, low-risk, single-scope work | Outcome, acceptance, delegated proportional verification, unresolved items |
| `light` | Multi-step/cross-component work, user-visible behavior, ambiguous acceptance, or substantial code change | Decisions, assumptions, risks, delegated verification plan, closure reflection |
| `full` | Security, privacy, authentication, financial, destructive, production-critical, multi-repository, multi-task, long-lived initiative, or explicitly requested work | Risk register, delegated falsification and independent-review evidence, detailed closure |

`set_governance_mode` appends an assessment with source `model` or
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

Light/full create advisory assessments only. They may guide the coordinator to
seek planning, review, or additional verification, but never create a backend
admission, profile, approval, stage, or closure gate. Finalized plan reports
and user decisions can still be linked to a downstream delegation as immutable
evidence when its declared work needs them; normal compact-reference, digest,
and project-isolation validation remains strict.

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

`record_initiative` creates a stable initiative or appends a revision. Status
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

`inspect_governance(task_ref=...)` uses the task only to anchor the project and
returns initiatives/links related to that task, the effective mode projection,
immutable initiative revision payloads, and a bounded timeline page. An
optional compact `initiative_ref` narrows the view; the durable `initiative_id`
is evidence only and does not authorize or gate the initiative.

## Plans and user decisions

A plan is a finalized `report_type=plan` report. Its review policy is
`informational` or `required`; an updated plan names a finalized predecessor
through `supersedes_report_ref` and receives a new immutable report ref and
content-manifest digest. Informational plans do not pause work. Required review
creates a coordinator-owned pause only for plan-dependent work when explicit
review or a real product, scope, destructive, external, security, privacy, or
risk decision is needed. It never changes public-tool availability.

`record_user_decision` appends `user_via_coordinator` evidence for an exact
task, plan, initiative, delegation, or report through one closed canonical
request containing task and subject refs/digest, decision type, English prompt,
exact original response, English response normalization, and asserted user
language. Decision types are `approve`, `reject`, `request_revision`,
`clarification`, `cancel`, `accept_risk`, and `override`. Plan/report decisions
require the canonical `sha256:<64-lowercase-hex>` subject digest; a plan must
already be finalized and completed. Only plan `approve` additionally requires a
current ready `approval_view` with its exact opaque handle, view digest, and
source sequence. Plan `request_revision` and `cancel` preserve the exact
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

`submit_governance_closure` appends a model-authored task or initiative
statement. The one closure shape requires `subject_type` (`task` or
`initiative`), the exact corresponding compact `subject_ref`, one `verdict`, and
bounded opaque JSON `evidence`; Cortex validates evidence only as finite JSON
within its size bound. The verdict is one of:

- `ready`;
- `ready_with_risks`;
- `not_ready`.

The subjects are not interchangeable. A supported task closure uses the exact
anchored `task_ref` as `subject_ref` and omits initiative-only
`initiative_status`; it may include opaque `completion_notes`. An initiative
closure uses the exact returned compact `initiative_ref` as `subject_ref` and
may include its supported initiative fields. Durable `task_id`/`initiative_id`
values are evidence only and are not callable closure inputs. `unresolved_risks`
and `follow_ups` are optional and default to empty lists. The closure call has
no subject digest argument. Required reports must be finalized and read before
a ready closure is attempted.

The verdict is a recommendation. A task closure can cite acceptance and
verification evidence, unresolved risks, and follow-ups. A long-lived
initiative closure can aggregate task/report evidence, achieved goals,
unfinished milestones, accepted exceptions, residual risk, retrospective, and
recommended future work.

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
the coordinator never calls `submit_report` or self-asserts
`documentation_not_required`.

The no-impact close has a deterministic evidence sequence. After every required
report is finalized, `record_initiative` creates or updates an initiative with
the `linked_task_refs` and `linked_report_refs` arrays, containing compact
`task_ref` and `report_ref` values, respectively, plus every other implementation
and verification report link. Closure evidence cites those
exact report references and returned digests. A report-only final initiative or
self-asserted no-impact value is invalid. The coordinator closes that exact
initiative, then calls `inspect_governance`
first in task scope and then in initiative scope. Both views must surface the
task relationship, required report links, and closure before the coordinator
claims a durable `ready` verdict. A failed write or inspection is disclosed as
an advisory limitation and does not block an honest final answer. Missing
documentation evidence can trigger rework, replacement, or risk disclosure; it
never becomes a backend lifecycle gate.

## Absolute nonblocking guarantee

No governance record, mode, initiative status, dependency warning, linked-task
state, report/plan status, user decision, review state, closure verdict, missing
decision, missing closure, or human-view state may:

- prohibit `create_delegation`;
- prohibit `read_delegation` or `read_reports`;
- prohibit `submit_report`;
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
final-answer availability without closure or a current view. The self-contained
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
