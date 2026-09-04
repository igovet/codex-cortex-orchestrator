# Storage classification

<!-- GENERATED:START -->

Status: current typed Cortex ledger, SQLite schema v2. Qualification status is
tracked in the [implementation checklist](typed-orchestration-integrity.md#11-completion-checklist),
not inferred from this architecture page.

## Decision

The project-sharded SQLite database is canonical coordination state. The model
chooses task meaning, decomposition and ready specialists; the deterministic
core enforces revision, dependency, artifact, ownership and decision integrity.
Neither the ledger nor its human views grant native filesystem, credential,
deployment or destructive-action authority.

The task-opening operation binds one existing canonical project directory.
Subsequent public operations use server-issued task or worker references.
A worker consumes immutable assignment scope before project work. Scoped product
requirements remain context, not a second assignment or publication-check list.

## Classification matrix

| Data | Canonical storage | Meaning |
| --- | --- | --- |
| Schema/project identity | SQLite pragmas, schema_migrations, v12_metadata | Current database family, schema and canonical project binding |
| Original task | tasks | Exact original request and language, linked outcomes, constraints and bounded context |
| Input inbox | source_submissions, source_consumptions | Immutable confidential source text, signed project/session-bound arrival records and one-shot ordered consumption; registered input hook captures passively, origin qualification/public bootstrap remain pending |
| Extraction drafts | registry_drafts, registry_draft_subjects, registry_draft_links, registry_draft_amendments | Incremental requirements, criteria, source ranges and append-only corrective receipts; current registry binding, no execution or semantic-approval authority |
| Effective contract | effective_contract_revisions, effective_contract_items, effective_contract_item_details | Append-only revisions and complete outcome details; steering never overwrites original evidence |
| Execution policy | execution_policies, governance_assessments | Assessment and decision boundary; advisory risk meaning does not grant host authority |
| Candidate plans | execution_graphs, plan_candidate_families, plan_candidate_selections | Immutable candidate graphs, alternatives and exact selected validated branch |
| Typed execution | execution_nodes, execution_assignments | Dependencies, capabilities, readiness, one owner, execution mode and fixed terminal kind |
| Native assignment binding | delegations, worker_capabilities | Profile/model/effort, exact protected native task correlation and connection-bound worker authority |
| Artifact state | artifact_generations, project_integrity | Observed generations, predecessor lineage and mutation/reconciliation barrier |
| Incremental verification | verification_receipts, verification_notes | Private signed execution observations and one declared-check explanation per receipt; immutable assignment/revision/generation binding, no standalone completion authority; native ingestion/public routing remain pending |
| Terminal evidence | reports, report_chunks, report_operations, execution_publications | One immutable typed body and one cross-kind terminal slot per assignment |
| Evidence reads | report_consumption_receipts | Bounded immutable report consumption; no authority transfer to a sibling |
| User decisions | user_decisions, clarification_bindings, clarification_holds, approval_handles | Original user response and current plan/closure/revision binding |
| Chronology | timeline | Transactional ordering, not a liveness or recovery substitute |
| Command receipts | Command receipt and idempotency records | Server-derived exact transition/result reconciliation, never caller-invented authority |
| Closure | governance_closures | Current evidence-backed verdict after mandatory post-result user review |
| Human-view metadata | projection_jobs, projection_files | Derived view work/freshness and digest state, not source report content |
| Backups | Owner-private sealed database/manifest bundles | Whole project-shard copies, not task-only exports |
| WAL/SHM | Adjacent SQLite files | Database machinery, not independent task evidence |
| Fingerprint manifests | Owner-private system-temp namespace | Worker scratch containing hashed observations, not file bodies or canonical reports |
| Skills/profiles | Installable plugin payload | Runtime policy and specialist guidance, not mutable task state |
| Repository docs | docs/project and docs/features | Source-backed navigation, not installed authority |
| Diagnostic streams | Same-user private log/journal | Bounded sanitized observation only; never committed or copied as raw reports |
| Host capability snapshots | Owner-private qualification directories | Immutable passive environment/capability evidence with digest; no human-authorship, dispatch or completion authority |

There are no initiative tables, initiative locators, initiative services or
initiative-scoped governance routes. Old outcome-owner tables, textual
assignment APIs, progress-publication routes and chunk-assembly APIs are not
compatibility interfaces.

## Schema integrity

Fresh state is created directly as the current schema in one transaction.
Existing databases must match the current application ID, schema record,
project metadata and required table shapes. Current governance/timeline columns
are checked exactly. Unsupported layouts fail closed without additive migration,
sentinel columns, import, directory relocation or recovery from historical data.

Canonical state changes and their events/receipts commit atomically. WAL supports
concurrent readers and serialized writes. A failed validation leaves no partial
assignment, report, revision or decision. Database, WAL and SHM files are
owner-private; symlinked or non-regular managed paths are rejected.

Original task fields, requirement identities, criterion details and revision
history have immutable database guards. Retirement requires a recorded revision
bound to a decision of that task; a decision cannot create a second requirement
revision. These guards do not establish that a coordinator-attributed decision
was actually received from a human. The independent source-input binding remains
an unfinished part of the revised implementation contract.

Do not edit database rows or schema metadata manually to force an old shard to
open. Historical installations and project data remain untouched.

## Reference integrity

Canonical internal IDs are private rows, not model-created public handles.
Task and worker references resolve exact project/actor relations; ambiguous,
foreign or copied authority fails closed. Native bootstrap correlation comes
from the exact server-rendered dispatch and supported host observations, never
the latest pending assignment or launch order.

A retained initial MCP catalogue does not bypass per-call actor validation.
Worker read and publication authority is tied to the consumed assignment.
A coordinator, sibling or new connection cannot take that slot by copying text.

Decision bindings preserve exact original responses. Plan review additionally
binds the candidate, independent validation, artifact state and assessment.
Approval selects only its validated graph. Closure requires a separate current
post-result review; no previous approval implies a close choice.

## Idempotency

The server derives command receipt identity from the durable transition.
An identical request after a genuinely ambiguous transport result reconciles
the existing commit. Changed content conflicts; successful dispatch never
permits a second spawn. Backend idempotency does not excuse an unexplained
successful mutation replay during live qualification.

Native filesystem and agent effects are outside SQLite transactions. The core
cannot roll them back or infer their success from a hook. Reconciliation observes
the actual project state after supported native quiescence.

## Artifact and revision integrity

Read-only assignments observe matching start/end fingerprints against their
sealed generation. Mutators seal successors with complete changed-path
commitments. Earlier generation-bound audits remain historical and cannot
satisfy newer verification or closure.

The shared project admits one mutator at a time; compatible ready readers can
run in parallel. Independent work is not serialized merely to hide correlation
or prerequisite bugs.

Direct user steering atomically advances the effective contract and invalidates
old authority. The response identifies affected protected native tasks; the
coordinator interrupts them through supported host calls and reconciles their
filesystem effects before new execution. Racing old publication returns a
non-publication state without consuming a terminal slot. It is not current
evidence or permission to retry.

Recovery reads current state, then required active continuations. Only complete
supported native evidence plus absent terminal publication establishes loss.
Timeout, silence and an isolated lifecycle stop are insufficient. Recovery,
reconciliation and remediation respect finite declared budgets and lineage.

## Derived human-readable task views

The backend writes no ledger, report, decision or cache state inside the product
project. Workers may of course create the requested product files there.

Private shard-local documents have the following layout:

```text
~/.codex/cortex/v12/projects/p-<hash>/
└── tasks/<task_ref>/
    ├── plans/
    │   ├── current.md
    │   └── revisions/<plan-report-id>.md
    └── reports/<report-id>.md
```

Shorter host-private content-addressed views provide verified user-facing links.
Only plan and finalized-report documents are published as Markdown. Task,
assignment, decision, governance and timeline records remain SQLite evidence.

A ready link requires contained regular-file, source-freshness and readback
digest checks. External edits become conflicts, not overwritten content.
Unknown report shapes fail rather than falling back to old renderers.

Publication commits its immutable report before derived-view I/O. One transient
I/O failure may repair the same expected bytes once inside the original request.
Persistent failures, unsafe paths and external-edit conflicts remain explicit.
Exact receipt reconciliation may subsequently repair the projection, never
create a second report. View failure does not erase durable worker evidence.

## Retention and privacy

Keep secrets, credentials, personal data and raw diagnostics out of reports,
human-view projections and repository files. Source-input records are confidential
canonical input, not telemetry: never emit their bodies in progress or error
output. Outside these protected original-source records, store only necessary
sanitized engineering facts. The registered root-input callback captures only
selected execution routes. Help, normal-mode and worker inputs are excluded;
capture does not prove human origin. No general conversation collection is authorized.
Worker prose is English; original user text retains its designated language.

Canonical data is retained until an explicitly authorized bounded maintenance
action. Projection pruning does not remove task evidence. Backups cover the
whole selected shard. Restore is offline, requires exact confirmations and
independently established quiescence, and must not run beside active MCP access.

Fingerprint scratch is not part of the durable backup. Its loss means unavailable
comparison evidence and uses the normal recovery/reconciliation boundary; it
never becomes an assumed matching fingerprint.

## References

- [Typed orchestration integrity](typed-orchestration-integrity.md)
- [Orchestration ledger](../features/orchestration-ledger/index.md)
- [Advisory governance](../features/advisory-governance/index.md)
- [Human-readable task views](../features/human-readable-task-views/index.md)
- [Operator maintenance](../features/operator-maintenance/index.md)
- [Security policy](../../SECURITY.md)
- [Verification](verification.md)

<!-- GENERATED:END -->
