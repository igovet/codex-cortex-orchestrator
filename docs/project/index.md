# Project overview

<!-- GENERATED:START -->

## Purpose

Cortex 1.14.16 is an explicit opt-in Codex plugin for durable multi-agent
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
solution plan. Planning is optional for genuinely minimal work. Light/full
delivery requires a `planner` worker stage whose finalized required-review
`plan` report and exact approval are predecessors for every delivery worker.
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
        ├── pass server-selected predecessor evidence through worker context
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
reviews, and verification. Before delegating project work, the host-injected
`AGENTS.md` context already governs the task. The coordinator reads
`docs/project/index.md`, `docs/features/index.md`, and only the
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
idempotency, transactions, uniqueness, SQLite integrity, monotonic connection
audiences, exact SubagentStart/PreToolUse-bound worker bootstrap, replay-safe
worker catalogue commitment, foreign-candidate isolation, ordered assignment-page receipts,
and immutable lost-assignment successor lineage. It does not enforce waves,
model-owned gates, profile-based capability admission, governance promotion, or
a server-selected recovery ladder. Exact packaged `profile_name` validation is
a prompt-integrity check.

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

V12 ships the Cortex activation guard and sanitized lifecycle observer. Review
and trust only the hook callbacks declared by the installed package. They are
host-side ordering, audience-correlation, and observation guardrails; the MCP
server independently owns connection roles, assignment evidence, publication,
reconciliation, and durable ledger authority.

## Public contract

The complete private registry contains fourteen tools: `open_task`,
`read_task`, `open_clarification`, `record_clarification`,
`open_plan_review`, `record_plan_review`, `open_steering`, `record_steering`,
`open_assignment`, `publish_plan`,
`publish_result`, `publish_documentation`, `assess_governance`, and
`close_task`. Coordinator `tools/list` exposes coordinator operations plus
`read_task`; a signed worker-candidate or worker receives only `read_task` and
the three publication operations. The MCP server authorizes that boundary
independently of discovery.

The active MCP registry owns exact shapes. `open_task` alone accepts the
resolved `project_root` and returns preferred compact `task_ref`; its canonical
`task_id` is durable evidence only. The seven task-anchored tools use `task_ref`
to locate and validate the project ledger. `open_assignment` returns the
server-rendered worker bootstrap, and publication calls use the task anchor with
server-bound worker context; no public call accepts a durable `*_id`.
No host
metadata, hook, thread identity, or plugin working directory supplies the root.
The task persists the exact `user_request_original` and `user_language` beside
the English `objective`, active contract version, independent outcomes,
constraints, linked acceptance criteria, a non-derived verification plan, and
optional arbitrary-JSON `context`.
The native worker brief is a compact bootstrap carrying only delivery context;
the mandatory first assignment read supplies the full common policy, profile
guidance, task evidence, and assignment scope. The saved root is used only for
project working context.
Each task outcome has a non-empty meaningful English requirement and linked
acceptance criteria before task creation; acceptance is not copied into a
standalone verification obligation, and optional context cannot replace an
outcome. Every delegation carries
the exact six-part knowledge block once, in order, with non-empty values before
native spawn. A successful `open_assignment` returns one compact closed native
dispatch plus replay state, including exact effort and an explicit model only
for non-default Terra or Sol. Codex
forwards it unchanged to the active host spawn operation. The `read_task`
assignment view is the server-owned
full-policy/evidence surface and is required as the worker's first task read on
both healthy and recovery paths.
Its compact reconciliation header exposes exact public outcome selectors before
the potentially large policy body, and medium or large structured results are
not redundantly copied into text. Evidence pagination is server-owned;
continuation is legal only immediately after the identical prior page returned
`has_more=true`. Terminal replay reconciles the existing receipt without a
second receipt or timeline mutation.

`publish_plan`, `publish_result`, and `publish_documentation` record immutable
worker-owned evidence through semantic publication operations. The server owns storage,
completion, and replay identity; each delegation/report-kind slot has one
terminal outcome, and changed payloads require recovery/rework. Plans persist a
server-derived `informational` or `required` review policy; the worker
publication does not choose or supply it.
Only the native worker that owns the delegation calls the applicable `publish_*` operation; its
completion handoff returns a concise `Summary` and exact `Report ref`. The
coordinator dispatches, waits, and consumes that handoff without rereading the
body merely to summarize it. A downstream worker reads finalized evidence only
when its declared work requires the body. Compact refs, durable IDs, digests,
and cursors are opaque byte-for-byte return data; only compact refs are
callable public locators, while durable IDs are non-callable evidence.
`read_task` is the bounded public task view: it returns server-produced state,
assignment, or evidence data with private ledger identity removed. A fresh
worker starts with the exact assignment view and continues only through the
server-owned continuation. Large evidence is bounded by the advertised read
contract; callers do not supply report-reference or consumer-delegation
locators. Derived Markdown rendering similarly preflights its aggregate output
limits (512 files, 32 MiB total, 10 MiB per file) to avoid partial output.
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
Inspection reads use the advertised `read_task` continuation and expose
semantic data; these reads create no public receipt.

For continuation calls, the server-owned continuation returned by `read_task`
is copied byte-for-byte only to that operation's continuation input. Durable
IDs, digests, and continuation values are evidence or resumption data, never
capabilities or substitutes for a callable task reference.

`read_task` also projects the current effective outcome contract. Each stable
`o_` item reference represents one independent user outcome. Acceptance and
verification criteria, task constraints, steer extensions, and exact source
fragments remain linked metadata and never become extra coverage items. A
delegation assigns each relevant outcome as owned, contributing, or
evidence-producing; current ownership is
non-overlapping. Finalized v2 reports may claim structured coverage only for
items assigned to their delegation at the current revision. A user `steer`
decision creates the next effective-contract revision. Complete unpaired
additions become independent top-level outcomes, while exactly one retire plus
one add is an atomic replacement. Unaffected outcomes and evidence remain
current. One transactional owner is retained per outcome, allowing parallel
delivery only across distinct outcomes and rejecting same or ambiguous
ownership. Aggregate coverage classifies each active item
as complete, missing, partial, unverified, stale, or contradictory. The
conformance projection relates that current revision, user decisions,
finalized-report manifests, completed coordinator-read digests, and aggregate
coverage. These are advisory evidence for model-owned rework and closure
reasoning, never a backend lifecycle or permission gate.

For current V3 worker publications, assignment bootstrap also emits a
server-owned ordered reconciliation receipt. Its template names every required
scope item without supplying a status or verification claim; the worker must
preserve the rows and match their count and ordered references before the first
publication attempt. This prevents a report handoff from silently dropping a
scope item while leaving every disposition evidence-owned by the worker.
Steering additions name an active outcome and create a replacement revision
whose linked metadata includes the new user fragment and decision relation;
they do not create parallel coverage obligations.

Private/internal decision references may match an `u_[0-9a-f]{12}` storage
pattern, but they remain non-callable evidence and never replace the public
task reference.

The matching narrow decision record operation appends coordinator-asserted ordinary-chat evidence, not
backend authority. Its public request is task-ref-only: it carries the exact
response and user language, plus steering outcome changes where applicable. The
original response is retained exactly; there are no translated duplicate fields.
Private subject, revision, digest, and ready-view bindings are resolved by the
server. Missing, renamed, extra, or cross-mixed fields are rejected before
mutation.

The native dispatch projection does not select an isolated worktree or
workspace. Physical concurrent-writer isolation is therefore an unconfirmed
host capability outside the ledger; Cortex does not implement or claim
collision prevention until a supported host mechanism and lifecycle owner are
supplied.
Delegation `scope` is required non-empty text defining the concise
worker-ownership boundary, while execution detail belongs in `instructions`;
object-shaped scope is invalid. `close_task` is task-anchored and accepts the
exact `task_ref`, one advisory `verdict`, and bounded evidence; durable
`task_id` is evidence only.

The task-facing result separates neutral finalized-report evidence from advisory
bookkeeping. `read_task` returns `execution_outcome` with
`evidence_status`, `finalized_report_count`, `completed_report_count`,
`effective_revision`, `coverage_status`, and `outcome`. The finalized count
includes every finalized report, while the outcome is derived deterministically
from current effective-contract coverage, excluding historical/superseded claims
and report arrival order, without claiming native lifecycle. It returns
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

Closure review is distinct from ordinary clarification. After the current
result is presented, exactly two localized choices are offered: revise the
same task or close it. Revision preserves the same `task_ref`; any later
assignment, report, or decision stales a previously consumed close choice. The
public `close_task` path atomically requires the current consumed close choice,
while internal advisory storage may remain policy-neutral. This boundary rule
does not schedule work or block a truthful final answer.

After a worker verifies the project result, the coordinator evaluates
documentation impact from reports. Material behavior, architecture, interface,
command, verification, convention, or feature-ownership changes require a
documentation-sync worker and a separate documentation verifier for the
harvest pages under `docs/project/` and `docs/features/`. Otherwise the
coordinator uses a finalized worker-owned report with an explicit English
documentation-impact section and material/no-impact rationale and does not
create an empty documentation edit. When existing finalized reports do not
contain that section, one bounded evidence-synthesis worker submits it. The
private/internal initiative bookkeeping links the exact task and documentation-impact
evidence; closure evidence remains task-scoped and cites server-produced evidence.
A
self-asserted `documentation_not_required` value is invalid. This stage
precedes closure; missing documentation evidence may
cause model-owned rework, replacement, or disclosed risk, never a backend gate.

Mutation replay identity is server-owned. Callers do not supply a replay key;
the ledger reconciles identical semantic writes and rejects
conflicting writes without treating replay as authentication or permission.

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
