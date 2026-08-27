# Project overview

<!-- GENERATED:START -->

## Purpose

Cortex 12.0.0 is an explicit opt-in Codex plugin for durable multi-agent
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
        ├── pass immutable report/decision IDs as evidence
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
enforce waves, gates, plan authority, capabilities, receipts, host lifecycle,
profile-based capability admission, governance promotion, or a recovery state
machine. Exact packaged `profile_name` validation is a prompt-integrity check.

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
- User installation: GitHub Marketplace flow in [README.md](../../README.md)
- Contributor source synchronization: [sync-cortex.sh](../../scripts/sync-cortex.sh)

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
resolved `project_root` and returns preferred `task_ref` plus canonical
`task_id`; the seven task-anchored tools use `task_ref` to locate and validate
the project ledger, while delegation/report paths derive their owner. No host
metadata, hook, thread identity, or plugin working directory supplies the root.
The task persists the exact `user_request_original` and `user_language` beside
the English `objective`, active contract version, requirements, constraints,
acceptance criteria, verification plan, and optional arbitrary-JSON `context`.
The native worker brief carries the saved root only for project working context.
The four task/result arrays are non-empty meaningful English contracts before
task creation; optional context cannot replace one. Every delegation carries
the exact six-part knowledge block once, in order, with non-empty values before
native spawn. Its returned native-dispatch payload is copied byte-for-byte into
exactly one matching host spawn; Luna omits only the model override, all efforts
are explicit, and one worker is never reused across durable delegations.

`submit_report` records immutable `progress`, `result`, `synthesis`, or `plan`
evidence. It supports `single`, `begin`, sequential `append`, `finalize`, and
`abort` modes; plans declare `informational` or `required` review policy.
Only the native worker that owns the delegation calls `submit_report`; the
coordinator dispatches, waits, and reads finalized evidence. Every ID, digest,
and cursor is opaque byte-for-byte return data for model callers.
`read_reports` is the only report body/chunk reader: it accepts 1–20 unique
known IDs in request order and resumes bounded section reads using its returned
cursor. Large reports are never returned as one unbounded body.
Inspection reads use `after_sequence` plus `limit`, expose compact references,
and return `next_sequence` with `has_more`; reads create no receipts.

`record_user_decision` appends coordinator-asserted ordinary-chat evidence, not
backend authority. It preserves an exact `*_original` response alongside English
normalization, language, subject binding, and the required immutable digest for
plan/report subjects. A plan approval binds only the exact finalized plan
revision and digest. Delegation `scope` is required non-empty text defining the
concise worker-ownership boundary, while execution detail belongs in
`instructions`; object-shaped scope is invalid. Closure requires `subject_type`
plus the existing `subject_id`.

After a worker verifies the project result, the coordinator evaluates
documentation impact from reports. Material behavior, architecture, interface,
command, verification, convention, or feature-ownership changes require a
documentation-sync worker and a separate documentation verifier for the
harvest pages under `docs/project/` and `docs/features/`. Otherwise the
coordinator uses a finalized worker-owned report with an explicit English
documentation-impact section and material/no-impact rationale and does not
create an empty documentation edit. When existing finalized reports do not
contain that section, one bounded evidence-synthesis worker submits it. The
final initiative links the exact task, that documentation-impact report ID, and
every other required report; closure evidence cites their exact IDs and returned
digests before task-scoped and initiative-scoped governance inspection. A
self-asserted `documentation_not_required` value is invalid. This stage
precedes closure; missing documentation evidence may
cause model-owned rework, replacement, or disclosed risk, never a backend gate.

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
