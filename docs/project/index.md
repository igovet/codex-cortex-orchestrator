# Project overview

<!-- GENERATED:START -->

## Purpose

Cortex 12.1.0 is an explicit opt-in Codex plugin for durable multi-agent
coordination. The installable product lives under
[plugins/cortex](../../plugins/cortex/). Repository-root scripts, tests, and
documents support development but do not define installed behavior.

V12 is a coordination ledger, not a workflow engine. The backend stores exact
versioned task/result contracts, delegations, immutable reports, ordinary-chat
user-decision evidence, governance assessments, project-level initiatives,
links, revisions, warnings, closures, and derived human-readable views. Workers
own project decomposition; the coordinator owns only orchestration parallelism,
profile/model/effort selection, mode revisions, verification depth,
documentation-impact decisions, rework, genuine user questions, interaction
holds, and the final decision. The root coordinator
is an orchestration-only control plane: every project action and substantive
domain analysis is worker-owned; its sole project-read exception is the bounded
orchestrator-owned knowledge route used to compile delegation requirements.

The core invariant is nonblocking governance: mode, initiative status,
dependency warnings, report state, closure verdict, or missing closure can
inform reasoning but cannot prohibit the next safe meaningful step.

The coordinator owns only a model-owned dynamic orchestration DAG. It persists
the current projection and evidence-backed revisions through the existing
task-linked initiative revision and delegation/report/decision graph, without
adding a tool or creating a backend workflow engine. It does not write a project
solution plan. Planning is an optional `planner` worker stage; its finalized
immutable `plan` report is the predecessor for every plan-dependent worker.
Evidence may add, remove, reorder, retry, or parent-link rework stages without
rewriting completed reports. The model uses retained
advisory C1/C2/C3 baselines—bounded low-risk / multi-step or cross-surface /
high-risk or cross-domain—normally mapped to `minimal` / `light` / `full`
governance depth. They are neither backend waves nor user-approval gates.

## Architecture

```text
explicit user activation
        │
        ▼
coordinator model ── classify/revise C1 | C2 | C3 and minimal | light | full
        │
        ├── create exact task/result contract and advisory assessments
        ├── route bounded knowledge and compile delegation contracts
        ├── construct/follow/adapt a worker-owned-stage DAG
        ├── planner worker → durable plan report → plan-dependent nodes
        ├── choose exact profile/model/effort per worker
        ├── pass compact report_ref/decision_ref links as evidence
        ├── adapt, delegate verification/rework, or request a real decision
        ├── conditionally delegate docs sync + docs verification
        └── submit advisory closure and synthesize the user answer
        │
        ╰── never inspect source/code, author a project plan, edit, build, test,
            or directly verify
                          │
                          ▼
        project-isolated SQLite schema-v1 sidecar
```

Workers own all project discovery, source/code/configuration access, domain
analysis, implementation, documentation edits, commands, builds, tests,
reviews, and verification. Before delegating project work, the coordinator must
read every applicable
`AGENTS.md`, `docs/project/index.md`, `docs/features/index.md`, and only the
task-relevant pages linked from those indexes for routing. The bundled
orchestrator alone defines that route and compiles the six-part per-delegation
knowledge contract: documents to consume first, applicable requirements,
verification contract, ownership constraints, known documentation state, and
the explicit further-discovery boundary. Profiles consume that supplied contract
without independently rerouting or reconstructing the route. Otherwise the
coordinator reasons from user input, ledger records, and worker reports; missing
evidence creates another focused delegation, never direct project investigation.
Ordinary delegations select an exact packaged `profile_name` and verify loaded
renderer proof plus digest. The separate human-readable `role` is not profile
proof; unavailable fallback is limited to a degraded non-durable dispatch and
explicitly carries a complete role contract.

That exception is a closed direct-read allowlist: each read names an
already-known exact allowed path and uses a non-shell direct reader. Shell,
commands, `rg`, `find`, globs, graph/source/repository search, directory listing,
and candidate-path probing are never coordinator routing operations. Unknown
roots or paths are delegated. Workers also own Git/manifests/caches/worktrees,
existence/absence or unchanged-state, and project-local `.codex` checks,
including when the user asks the coordinator to perform one.

The sidecar enforces strict schemas, reference existence, project isolation,
idempotency, transactions, uniqueness, and SQLite integrity. It does not
enforce waves, gates, plan authority, capabilities, receipt-gated lifecycle,
host lifecycle, profile-based capability admission, governance promotion, or a
recovery state machine. Worker handoff reads may still emit immutable delivery
receipts; those receipts are evidence, not enforcement. Exact packaged
`profile_name` validation is a prompt-integrity check.

## Stack and entry points

- MCP facade: [cortex.py](../../plugins/cortex/scripts/cortex.py)
- Public catalog: [public_contracts.py](../../plugins/cortex/scripts/cortex_runtime/public_contracts.py)
- V12 service: [v12_service.py](../../plugins/cortex/scripts/cortex_runtime/v12_service.py)
- Schema-v1 store: [v12_store.py](../../plugins/cortex/scripts/cortex_runtime/v12_store.py)
- Model transport: [model_routing.py](../../plugins/cortex/scripts/cortex_runtime/model_routing.py)
- Operator maintenance: [v12_maintenance.py](../../plugins/cortex/scripts/cortex_runtime/v12_maintenance.py)
- Advisory profile registry: [profiles.json](../../plugins/cortex/profiles.json)
- Orchestration skill: [orchestrator/SKILL.md](../../plugins/cortex/skills/orchestrator/SKILL.md)
- Control semantics: [cortex-control/SKILL.md](../../plugins/cortex/skills/cortex-control/SKILL.md)
- Coordinator communication: [coordinator-communication/SKILL.md](../../plugins/cortex/skills/coordinator-communication/SKILL.md)
- User installation: GitHub Marketplace flow in [README.md](../../README.md)
- Contributor source synchronization: [sync-cortex.sh](../../scripts/sync-cortex.sh)
- Isolated candidate launcher: [cortex-dev](../../scripts/cortex-dev)
- Candidate reset helper: [cortex-dev-reset](../../scripts/cortex-dev-reset)

## Runtime requirements

- Python 3.11+ with the standard library only;
- Codex Plugins and multi-agent support;
- `features.multi_agent_v2 = true`;
- `agents.default_subagent_model = "gpt-5.6-luna"`;
- macOS or Linux, with WSL recommended on Windows.

V12 ships no lifecycle hooks and requires no hook-trust flow.

## Public contract

The same eleven tools are visible to every participant: `create_task`,
`inspect_task`, `create_delegation`, `read_delegation`, `submit_report`,
`read_reports`, `set_governance_mode`, `record_initiative`,
`inspect_governance`, `submit_governance_closure`, and
`record_user_decision`.

The active MCP registry owns exact shapes. `create_task` alone accepts the
resolved `project_root` and returns preferred compact `task_ref`; its canonical
`task_id` is durable evidence only. The seven task-anchored tools use `task_ref`
to locate and validate the project ledger. `read_delegation` uses
`delegation_ref`, `submit_report` uses `delegation_ref`/`report_ref`, and
`read_reports` uses `report_refs`; no public call accepts a durable `*_id`.
No host
metadata, hook, thread identity, or plugin working directory supplies the root.
The task persists the exact `user_request_original` and `user_language` beside
the English `objective`, active contract version, requirements, constraints,
acceptance criteria, verification plan, and optional arbitrary-JSON `context`.
The native worker brief carries the saved root only for project working context.
The four task/result arrays are non-empty meaningful English contracts before
task creation; optional context cannot replace one. Every delegation carries
the exact six-part knowledge block once, in order, with non-empty values before
native spawn. A successful `create_delegation` returns root-level
`native_dispatch` and `renderer` proof; the complete rendered worker message
occurs only once at `native_dispatch.native_arguments.message` and is copied
byte-for-byte into exactly one matching host spawn. `read_delegation` is the
verbose recovery surface and is not required on the healthy path. Luna omits
only the model override, all efforts are explicit, and one worker is never
reused across durable delegations.

`submit_report` records immutable `progress`, `result`, `synthesis`, or `plan`
evidence. A bounded one-chunk report uses `single`; larger reports use `begin`,
sequential `append`, then `finalize`, or `begin` followed by `abort`. Plans
declare `informational` or `required` review policy.
Only the native worker that owns the delegation calls `submit_report`; its
completion handoff returns a concise `Summary` and exact `Report ref`. The
coordinator dispatches, waits, and consumes that handoff without rereading the
body merely to summarize it. A downstream worker reads finalized evidence only
when its declared work requires the body. Compact refs, durable IDs, digests,
and cursors are opaque byte-for-byte return data; only compact refs are
callable public locators, while durable IDs are non-callable evidence.
`read_reports` is the only report body/chunk reader: it accepts 1–20 unique
known report refs in request order and resumes bounded section reads using its
returned cursor. A coordinator call returns report metadata/manifests only;
body/chunk content requires a consuming worker's exact
`consumer_delegation_ref`, and that delegation must declare each input report.
Large reports are never returned as one unbounded body. Worker body reads
produce structural consumption receipts; coordinator metadata reads do not.
The service preflights aggregate report request/response budgets before
materializing bodies. Derived Markdown rendering similarly preflights its
aggregate output limits (512 files, 32 MiB total, 10 MiB per file) to avoid
partial output.
Canonical product-facing report bodies support the fixed
`cortex/report/{progress,result,synthesis,plan}/v1` schemas and the additive
`cortex/report/{result,synthesis,plan}/v2` schemas. V2 adds structured
`contract_coverage`, deviations, unresolved items, risks, and verification;
v1 and storage-valid legacy evidence remain readable and immutable. A canonical
body may carry one optional unchanged `source_text` value, without a language
tag or a translated/original duplicate. Only a finalized completed
semantic-valid canonical plan receives a ready approval relation. Planner-authored
implementation microtasks are evidence for the model-owned DAG only, never
backend jobs, scheduling gates, or worker-subtask To-Do entries.
Inspection reads use `after_sequence` plus `limit`, expose compact references,
and return `next_sequence` with `has_more`; these inspections create no
receipts. A worker handoff `read_reports` read may create an immutable page
receipt for the exact consuming delegation.

For continuation calls, `handles.after_sequence`, `handles.chunk_index`, and
`handles.idempotency_key` are copied byte-for-byte only to their literal named
inputs. `handles.cursor` is the separate opaque `read_reports` continuation
value. Root receipt fields `next_sequence` and `next_chunk_index` are
informational and are not `handles` aliases; neither is `retry_handle` a
substitute for a callable handle.

`inspect_task` also projects the current effective outcome contract. Its stable
`o_` item references cover the original requirements, constraints, acceptance
criteria, and verification expectations. A delegation assigns each relevant
item as owned, contributing, or evidence-producing; current ownership is
non-overlapping. Finalized v2 reports may claim structured coverage only for
items assigned to their delegation at the current revision. A user `steer`
decision creates the next effective-contract revision, retiring only the named
items and adding the stated replacements, so unaffected item references and
their evidence remain current. Aggregate coverage classifies each active item
as complete, missing, partial, unverified, stale, or contradictory. The
conformance projection relates that current revision, user decisions,
finalized-report manifests, completed coordinator-read digests, and aggregate
coverage. These are advisory evidence for model-owned rework and closure
reasoning, never a backend lifecycle or permission gate.

Canonical compact decision references match `u_[0-9a-f]{12}` and remain opaque
evidence references, copied exactly from a successful receipt.

`record_user_decision` appends coordinator-asserted ordinary-chat evidence, not
backend authority. Its one canonical request contains `task_ref`,
`subject_type`, `subject_ref`, `decision_type`, neutral `prompt`, exact
arbitrary-Unicode `response_original`, and `user_language`. The original
response is retained exactly; there are no `prompt_en` or `response_en`
fields. It preserves the response, language, subject binding, and required
immutable digest for plan/report subjects; task, delegation, and initiative
decisions omit that digest. Only plan approval additionally binds a current ready approval view
with `approval_handle`, `approval_view_content_digest`, and
`approval_view_source_sequence` copied from one returned relation. A ready plan
read also provides `handles.decision_binding` with exact decision-input names; plan
revision/cancellation feedback preserves the exact plan digest and response
without volatile view binding. Missing, renamed, extra, or cross-mixed fields
are rejected before mutation.

The native dispatch projection does not select an isolated worktree or
workspace. Physical concurrent-writer isolation is therefore an unconfirmed
host capability outside the ledger; Cortex does not implement or claim
collision prevention until a supported host mechanism and lifecycle owner are
supplied.
Delegation `scope` is required non-empty text defining the
concise worker-ownership boundary, while execution detail belongs in
`instructions`; object-shaped scope is invalid. Closure requires `subject_type`
plus the existing compact `subject_ref`; durable `subject_id` is evidence only.

The task-facing result separates neutral finalized-report evidence from advisory
bookkeeping. `inspect_task` returns `execution_outcome` with exactly
`evidence_status`, `finalized_report_count`, `completed_report_count`, and
`outcome`. The finalized count includes every finalized report; the completed
count includes semantically valid canonical finalized results with status
`completed`. `outcome` is `null` before the first semantically valid canonical
finalized result and then reflects the latest such result as `completed` or
`incomplete`, without claiming native lifecycle. It returns
`advisory_closure`
(`record_status` and `latest_record`, or `null`). After
sufficient finalized evidence, the coordinator selects `ready`,
`ready_with_risks`, or `not_ready`, then automatically attempts the advisory
write and intended bounded inspection. `ready_with_risks` needs no user
confirmation. The closure result returns `closure_confirmation` with
`inspection_status`, `reason`, and `attempts`; only one same-idempotency retry
is allowed for a verified transient persistence or inspection failure. An
`unconfirmed` result is advisory uncertainty only and does not change
`execution_outcome` evidence.

After a worker verifies the project result, the coordinator evaluates
documentation impact from reports. Material behavior, architecture, interface,
command, verification, convention, or feature-ownership changes require a
documentation-sync worker and a separate documentation verifier for the
harvest pages under `docs/project/` and `docs/features/`. Otherwise the
coordinator uses a finalized worker-owned report with an explicit English
documentation-impact section and material/no-impact rationale and does not
create an empty documentation edit. When existing finalized reports do not
contain that section, one bounded evidence-synthesis worker submits it. The
final initiative links the exact task, that documentation-impact `report_ref`,
and every other required report; closure evidence cites their exact compact refs
and returned
digests before task-scoped and initiative-scoped governance inspection. A
self-asserted `documentation_not_required` value is invalid. This stage
precedes closure; missing documentation evidence may
cause model-owned rework, replacement, or disclosed risk, never a backend gate.

The first ledger mutation may omit `idempotency_key`; its receipt returns a
server-issued `handles.idempotency_key` for an exact retry. Retrying the same
operation with the same normalized payload replays the original result;
reusing a key with a different payload is a non-mutating conflict. This is
caller retry safety, not authentication or permission.

## Storage

Each resolved project root uses:

```text
~/.codex/cortex/v12/projects/p-<sha256-of-resolved-project-root>/cortex.db
```

The new schema is version 1. V11 databases remain byte-for-byte untouched and
are not migration inputs. V11 tools and unfinished V11 tasks are incompatible
with V12.

The canonical database can produce only derived, host-private Markdown task
views beside its shard. No Cortex database, report, decision, projection, or
other state is written beneath `project_root`, including project-local `.codex`.
Only a current digest-verified `ready` view exposes its returned absolute path
as a clickable link with a localized summary and next step; `stale`, `conflict`,
`unavailable`, and `disabled` never do. See
[Human-readable task views](../features/human-readable-task-views/index.md).

## Feature registry

- [Orchestration ledger](../features/orchestration-ledger/index.md)
- [Advisory governance and initiatives](../features/advisory-governance/index.md)
- [Plugin packaging and validation](../features/plugin-packaging/index.md)
- [Coordinator communication](../features/coordinator-communication/index.md)
- [Knowledge-route contract](../features/knowledge-route-contract/index.md)
- [Human-readable task views](../features/human-readable-task-views/index.md)
- [Operator maintenance](../features/operator-maintenance/index.md)
- [Release readiness](../release-readiness.md)

## Project references

- [Conventions](conventions.md)
- [Architecture decisions](decisions.md)
- [Storage classification](storage-classification.md)
- [Gotchas](gotchas.md)
- [Verification](verification.md)
- [Security policy](../../SECURITY.md)

<!-- GENERATED:END -->
