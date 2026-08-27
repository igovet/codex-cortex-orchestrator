# Release readiness

Status: source-mode release contract for Cortex 12.0.0.

## Current release identity

- release label: 12.0.0
- coordination contract: V12 durable, nonblocking ledger
- SQLite schema: v1 in the new V12 namespace
- public facade: exactly eleven action-specific MCP tools
- public audience: identical for coordinators and workers
- runtime model contract: bundled `orchestrator` and `cortex-control` skills
- profiles: advisory role templates
- governance: model-owned and advisory
- coordinator: orchestration/delegation plus the bounded orchestrator-owned
  knowledge route; no source/project action or substantive task work
- lifecycle hooks: absent
- operator maintenance: task-anchored host-private CLI outside MCP; restore is
  offline only

The acceptance criterion is simple and strict: governance may deepen reasoning,
verification, and the final explanation, but no governance record or missing
record may prohibit the model from taking the next safe meaningful step.

## Public contract

`tools/list` must contain exactly these names, in the canonical registry order:

1. `create_task`
2. `inspect_task`
3. `create_delegation`
4. `read_delegation`
5. `submit_report`
6. `read_reports`
7. `set_governance_mode`
8. `record_initiative`
9. `inspect_governance`
10. `submit_governance_closure`
11. `record_user_decision`

The catalog is identical for every participant. There is no audience filter,
capability matrix, host-bound authority, tool-name alias, or action selector. Each input
schema is closed and is also the runtime validator's source; every tool also
advertises its successful `outputSchema`, which runtime validates before a
success is transported as JSON text plus `structuredContent` with
`isError=false`. Caller-correctable errors are bounded sanitized text-only
`isError=true` results with no `structuredContent`; server-state failures are
sanitized JSON-RPC internal errors.

Only `create_task` accepts the exact resolved `project_root` and stores the
canonical project association; it is the sole public project-root boundary. All
other ten tools require its returned `task_id` and use that anchor to locate and
validate the saved project ledger; initiative calls use it only as a locator,
never as permission. The native worker brief carries the saved root only for
working-directory context. No root is inferred from MCP metadata, thread
identity, the plugin process `cwd`, or a lifecycle hook.

`create_task` is an exact, versioned task/result contract. It keeps the exact
arbitrary-Unicode `user_request_original` and `user_language` beside the
English-normalized internal `objective`, English bounded `requirements`,
`constraints`, `acceptance_criteria`, and `verification_plan`, plus
`task_contract_version` and optional bounded JSON `context`. The English
normalization does not replace the original request, and the result contract is
not a backend execution or permission gate. `context` never supplies or
overrides the root. `create_delegation.scope` is required non-empty text
(maximum 65,536 characters) describing the concise worker-ownership boundary;
detailed execution belongs in `instructions`, and object-shaped scope is
invalid. `create_delegation` also separates exact packaged `profile_name` from
the human-readable `role`, requires loaded renderer proof, and returns one exact
native-dispatch payload for one matching host spawn.
`submit_governance_closure` requires `subject_type` and the existing
task or initiative `subject_id`.

The root coordinator may use the ledger, user interaction, native worker
coordination, and worker reports to orchestrate and synthesize. It must never
inspect or search source/code/configuration, create or edit target-project
files, perform substantive domain analysis, or run project commands, builds,
tests, browser checks, or direct verification. Every such action is
worker-owned. Before project delegation, its only project-read exception must
follow the bounded route through applicable `AGENTS.md`, the project and feature
indexes, and task-relevant
linked pages. The orchestrator alone defines the exact route and six-part
per-delegation contract; profiles consume the supplied result without rerouting.

Each allowed read names an already-known exact path and uses a non-shell direct
reader. Shell/commands, `rg`, `find`, globs, graph/source/repository search,
directory listing, and candidate probes are never routing mechanisms. Unknown
roots or paths and unavailable direct reads require a worker. Project-root
discovery and project-local state or artifact checks are also worker-owned,
including Git, manifests, caches, worktrees, existence/absence or
unchanged-state, and project-local `.codex`. Read-only, pre-plan,
report-recovery, or explicit-user-request framing does not create coordinator
authority.

Core coordination tools must not read governance mode, initiative status,
dependency warnings, report completion, closure verdict, or closure presence to
authorize an operation. The following states are explicit nonblocking release
cases:

- no governance closure exists;
- the latest closure is `not_ready`;
- an initiative remains open;
- a linked task is unfinished;
- a same-project dependency is unresolved or cyclic;
- a worker ends without a report or native stop observation.

In each case, the coordinator remains free to create a useful delegation, read
known reports, synthesize reported evidence, request a real user decision, or
provide a final answer. A user-requested plan review and any genuine external,
destructive, scope, acceptance, or product decision remain coordinator-owned
ordinary-chat holds, not backend gates. It does not fill an evidence gap by
directly inspecting or testing the project.

## Storage contract

V12 creates one database per resolved project root:

```text
~/.codex/cortex/v12/projects/p-<sha256-of-resolved-project-root>/cortex.db
```

The database must identify the V12 family, report `PRAGMA user_version = 1`,
contain the ordered `v12-initial` and `v12-schema-v1-human-views` additive
migration rows, and retain matching project-hash metadata. The schema contains
tasks, delegations, immutable reports and report
chunks, append-only governance assessments, user decisions that preserve exact
original text beside English normalization, initiatives, append-only initiative
revisions, current initiative links, immutable closures, an ordered timeline,
idempotency records, bounded projection jobs/files, and minimal metadata.

Release evidence must prove:

- first-use bootstrap is atomic;
- the one additive pre-release V12 migration preserves existing task rows and
  converts each legacy report body into one finalized canonical chunk without
  changing `PRAGMA user_version = 1`;
- concurrent reports, assessments, and initiative revisions are not lost;
- timeline sequences remain unique and ordered;
- each accepted chunk append emits a task-scoped `report_chunk_appended` event;
  a one-time normal-open repair appends only missing derived chronology with a
  `backfill` marker, preserves existing timeline/report evidence, refreshes
  affected private views best effort, and refuses to guess ambiguous report-only
  initiative lineage;
- identical idempotent replay returns the original record;
- conflicting replay returns non-mutating `idempotency_conflict`;
- report types include `progress`, `result`, `synthesis`, and `plan`; a report
  supports `single`, `begin`, `append`, `finalize`, and `abort`, with immutable
  sequential chunks, terminal finalization/abort, and explicit supersession;
- each chunk is bounded to 32 KiB; report assembly is bounded to 256 chunks and
  8 MiB; its final digest binds the complete immutable manifest;
- `read_reports` preserves requested order for 1–20 known IDs, returns only
  complete chunks within a 65,536-byte budget, and returns a selection-scoped
  cursor without duplication; `max_bytes=0` returns only assembly metadata;
  deprecated `byte_budget` is an equivalent compatibility alias and conflicts
  with a different simultaneous `max_bytes` value;
- inspection pages use `after_sequence` plus `limit`, return stable
  `next_sequence`/`has_more`, and expose compact report references while
  `read_reports` remains the only bounded report body/chunk reader;
- task/report/initiative links do not cross project ledgers;
- errors never expose raw task or report content;
- state/project directories are `0700`, database/WAL/SHM files are `0600`, and
  symlink or non-regular database paths are rejected before SQLite opens;
- an oversized stdio frame is drained with a sanitized error and does not
  prevent a following `ping` or `tools/list` request;
- SQLite integrity checks pass after concurrent operations.

## User decisions, plan review, and human views

`record_user_decision` records an ordinary-chat decision only when the
coordinator asserts that the user made one. The record keeps subject type and
ID, a subject digest when applicable, a decision type, English prompt context,
exact arbitrary-Unicode `response_original`, English `response_en`, user
language, attribution, and optional supersession. The English value supports
durable internal work; it never replaces the original response. Plan and report
decisions require the exact immutable digest; a plan decision also requires a
completed, finalized `plan` report. Only plan `approve` additionally requires a
current ready approval view and opaque approval handle. Plan `request_revision`
and `cancel` preserve the exact finalized plan digest and response without
volatile view binding, so intervening non-plan timeline events cannot block
feedback. The record binds evidence and scope but is not authentication, a
bearer approval token, or a backend lifecycle gate.

The current decision shape and the deprecated complete legacy plan-decision
shape are mutually exclusive. The legacy compatibility path requires all of
its fields; partial or mixed current/legacy input is rejected by the public
schema.

`report_type=plan` provides immutable plan evidence without adding a twelfth
tool. `review_policy=required` expresses a coordinator-owned ordinary-chat
review hold: present the finalized plan revision, digest, localized summary,
and approve/revise/cancel choices; then record an unambiguous response against
that exact revision. A revised plan is a new report and requires fresh review.
The backend never authorizes or blocks a later action because a decision,
approval, review record, or review policy is present, absent, or stale.

The database is canonical. Per-task Markdown files are derived host-private
views beside the V12 shard, never written to the target project or a
project-local `.codex` directory. A link may be published only after the active
tool returns it `ready` as a contained absolute regular file that is current
for its source sequence and digest-verified. Pair each verified clickable link
with a localized summary and its implication or next step. If creation,
freshness, or digest verification fails, omit the link, continue from canonical evidence,
and disclose the material human-view limitation without blocking safe work or
an honest final answer.

## Governance and initiative contract

Mode assessments use `minimal`, `light`, or `full` and source `model` or
`user_override`. An explicit override must be stored unchanged. Later mode
changes append a new assessment; the backend does not classify or overwrite a
prior row. The latest user override remains effective across later model
assessments, which may preserve an evidence-backed warning without replacing
the user's choice.

Initiative status is limited to `proposed`, `active`, `paused`, `completed`,
`closed`, and `cancelled`. Every transition among those values is accepted.
Missing or cyclic same-project dependencies persist with warnings. Neither
warning rejects a later revision or closure.

Task-anchored governance inspection must scope initiatives and links to that
task, return the effective mode projection, and expose immutable initiative
revision payloads in bounded chronological pages.

Closures use `ready`, `ready_with_risks`, or `not_ready`. The verdict is an
advisory model recommendation. A `not_ready` task must accept rework delegation
and reports. An initiative closure must be storable with unresolved dependency
risk. Missing closure must not block the final user answer.

## Model-routing contract

The coordinator independently selects the exact model/effort pair for each
delegation. Profiles and governance modes may inform judgment but do not
authorize, derive, or rewrite the pair.

Canonical recommendations are:

| Model | Recommended effort | Intended use |
| --- | --- | --- |
| `gpt-5.6-luna` | `high` | Default bounded work |
| `gpt-5.6-terra` | `high` | Genuinely complex non-security work |
| `gpt-5.6-sol` | `high` | Security work and security-focused review |

Every model supports `low`, `medium`, `high`, `xhigh`, and `max`. Native
projection uses `fork_turns="none"` and preserves the effort. Logical Luna
omits the native `model` argument because it is the configured default; Terra
and Sol carry their exact model overrides. The server must have no automatic
model replacement or Luna → Terra → Sol recovery ladder. Each successful
durable delegation returns one native-dispatch payload whose native arguments
are copied byte-for-byte into exactly one matching host spawn; no ad-hoc prompt,
shared worker, missing spawn, or duplicate spawn is acceptable.

## Operator maintenance contract

The packaged `cortex_runtime.v12_maintenance` module remains outside
`tools/list`; it cannot change the eleven-tool catalog. Every command starts
from one exact V12 `task_id`, derives its shard and host-private targets from
that ID, accepts no root/arbitrary path/V11 target, validates the owner-only
filesystem and complete V12 database identity, and emits bounded sanitized
JSON.

Release evidence must cover read-only `health`; sealed whole-project-shard
online backup; confirmed checkpoint/optimize/vacuum; exact-task projection
regeneration; dry-run-by-default safe projection prune and explicit backup
retention; invalid task/shard, symlink, permission, schema, manifest, digest,
and confirmation rejection; and zero `project_root`/V11 writes. Projection
prune may remove only exact registered non-ready derived Markdown and must
preserve ready, conflict, unmanaged, or digest-mismatched plan/report files plus
every canonical row. Retention may remove only 1–20 explicitly named complete sealed
backup bundles and must preserve the canonical database.

Restore is never online. Tests and documentation must require the exact backup
ID, task ID, `p-<hash>` shard, `RESTORE`, and `MCP_STOPPED`; that last value is
only an operator acknowledgement after all normal MCP access has actually been
stopped, not a cross-process lock. Restore must create a fresh recovery backup,
validate the selected backup and final live database, and return sanitized
failure/recovery outcomes.

## Package boundary

The installable package must include the manifest, MCP configuration,
eleven-tool facade and runtime, schema-v1 store, host-private operator
maintenance module, advisory profiles, bundled skills, direct MCP configuration,
and assets. It must not ship lifecycle hooks or lifecycle hook code.

The package and repository metadata must consistently identify Cortex 12.0.0,
schema v1, the nonblocking ledger, model-owned governance, advisory profiles,
and the exact eleven-tool catalog. Stale claims about waves, gates, capabilities,
plan authority, host epochs, read receipts, required wait/read order, lifecycle
HMAC, repair escrow, closure breakers, resource locks, required governance
workers, or server-owned recovery are release defects.

V11 state is a historical compatibility boundary only. V12 must not open,
migrate, delete, or modify V11 databases. V11 tools and unfinished V11 tasks
are incompatible with V12 and cannot serve as fallback authority.

## Release/protocol gate

Run the isolated release/protocol test:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B -m pytest -q tests/test_marketplace_release_gate.py
```

The test must build the explicit source candidate, compile bundled Python,
start the actual V12 MCP facade in isolated temporary projects and `HOME`, and
exercise the catalog, storage, nonblocking states, concurrent mutations,
idempotency, isolation, host-private projections, maintenance CLI, V11
preservation, hook absence, and model transport.

## Supporting source diagnostics

Run the smallest checks that prove the affected surface, then broaden for the
release candidate:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/cortex-prompt-lint.py
PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/validate-cortex-marketplace.py
PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/verify-cortex-release.py --mode source
git diff --check
./scripts/sync-cortex.sh --dry-run
```

Before publication, verify the committed candidate too:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/verify-cortex-release.py --mode head
```

The self-contained skill/profile lint is structural source evidence only. It
must cover the bundled coordination, governance, routing, knowledge-contract,
coordinator-only, textual-scope, and conditional-documentation invariants. It
must semantically reject coordinator shell/search/graph routing and
project-local artifact/state authority. It must also mutation-test empty
task/result contracts, incomplete six-part delegation blocks, constructed
IDs/digests, coordinator `submit_report`, MCP reads of `skill://` resources,
premature/task-subject no-doc closure, report-only final initiatives, ad-hoc or
cardinality-mismatched native dispatch, localized worker transcripts,
self-asserted no-impact closure, and free-form role text treated as loaded
profile proof, but it makes no model call. Real ordinary interactive Luna/high
tmux tasks provide the model-behavior evidence.

After worker-owned project verification, the coordinator must assess
documentation impact from reports. A material behavior, architecture,
interface, command, verification, convention, or feature-ownership change
requires a delegated documentation-sync update to the harvest documentation
under `docs/project/` and `docs/features/`, then delegated documentation
verification. A no-impact task requires a finalized worker-owned report with an
explicit English documentation-impact section and material/no-impact rationale,
links its exact report ID in the final initiative, and cites that ID plus its
returned digest in closure evidence. A self-asserted
`documentation_not_required` value is invalid. This
conditional stage precedes advisory closure and the final answer. Missing
documentation evidence leads to model-owned rework, replacement, or explicit
risk disclosure rather than a backend lifecycle gate.

Every source check is evidence only when actually run. A source candidate does
not prove an installed plugin, active Codex configuration, native subagent
behavior, or an interactive user flow.

## Installed and interactive verification

End users install or update through the README's GitHub Marketplace flow. For
an explicitly authorized final local-source synchronization from this checkout,
repository developers use:

```bash
./scripts/sync-cortex.sh
./scripts/sync-cortex.sh --check
```

Then start a new ordinary interactive Codex CLI session inside tmux. Do not use
`codex exec`:

```bash
tmux new-session -s cortex-v12-smoke
codex
```

Run exactly one fresh interactive Cortex session first and require it to reach
worker-verified acceptance plus advisory `ready` closure without a coordinator
boundary violation. Only after this single-session pass may concurrent
multi-session smoke begin.

Exercise several explicit `$cortex:orchestrator` tasks:

1. A bounded minimal task with concise acceptance and closure.
2. A light task revised to full after security evidence.
3. A risky task with an explicit user `minimal` override and a model warning.
4. A task that records `not_ready`, then creates model-owned rework.
5. A project initiative linking multiple tasks/reports and closing with a
   disclosed unresolved dependency.
6. A task where closure is deliberately absent or unavailable, followed by a
   complete user-facing answer.
7. A bounded knowledge-routing task where the coordinator reads only applicable
   `AGENTS.md`, the two knowledge indexes, and task-relevant linked pages;
   compiles all six contract parts; and dispatches a worker that does not reroute.
8. A Russian-user plan-review task where every worker commentary, message, final
   response, tool-authored durable string, and operational artifact remains
   English, the coordinator presents Russian
   summaries with verified immutable-revision and current-plan links, and an
   approve/revise/clarify decision binds the exact report digest.
9. A task that exercises chunked report resume and a non-ready/tampered human
   view; the coordinator publishes no stale path, summarizes canonical evidence
   inline, and still completes safely.
10. A required-plan task with an explicit ban on coordinator project operations;
    workers own all project grounding before the plan-review hold.
11. A reportless/rework task whose user asks about project-local `.codex`; the
    coordinator delegates the project-state check instead of searching it.
12. A task where all four task/result arrays are non-empty and every delegation
    has the exact six non-empty knowledge sections before native spawn.
13. A no-doc task where the owning worker submits a finalized report with an
    explicit English documentation-impact section and material/no-impact
    rationale, the final initiative links the exact task and that exact report
    plus all other required reports, closure evidence cites their exact IDs and
    returned digests, and both task-scoped and initiative-scoped governance
    verify the closure before any durable-ready claim.
14. An explicit activation with no `read_mcp_resource`, `resources/read`, or
    `skill://` MCP attempt, plus exact byte-for-byte reuse of every returned ID
    and digest.
15. Four durable delegations produce four matching host spawns copied exactly
    from their returned native-dispatch payloads, with correct fork isolation,
    model/effort fields, renderer/profile evidence, and worker-owned reports.
16. Ordinary delegations select exact packaged `profile_name` values and
    produce loaded proof/digests; the separate human-readable `role` is not
    profile proof, and a degraded non-durable fallback requires an explicit
    bounded role contract and unavailable-profile disclosure.

Confirm the installed `tools/list` is the same for coordinator and workers,
Luna is dispatched without a native model override, Terra/Sol are exact
overrides, no lifecycle hook trust or server recovery route appears, and the
final answer remains available in every advisory state.

Audit the coordinator's actual tool chronology: aside from Cortex ledger calls,
native agent coordination, user interaction, and the bounded orchestrator-owned
knowledge route, it must contain no target-project shell or command use,
`rg`/`find`/glob/graph/source/repository search, root discovery,
source/edit/browser/build/test action, or project-state/artifact verification.
Allowed knowledge reads are non-shell direct reads of already-known exact paths.
Each project action must be attributable to a worker delegation and report.
Exercise both documentation branches: one
material task with a documentation-sync worker plus an independent
documentation verifier, and one no-impact task with an explicit
English documentation-impact rationale in a finalized worker-owned report, no
documentation churn, the exact report link in the initiative, and exact report
ID/digest citations in closure evidence. Reject coordinator self-assertion.
Verify that Cortex writes no file or directory below `project_root`; every
published task/plan/report/decision/timeline link must point to a current,
digest-verified regular file inside the host-private V12 task subtree and must
be paired with a localized summary.

Interactive smoke is separate installed-host evidence. Record the exact session
environment, tasks exercised, relevant sanitized outcomes, and every scenario
not run. Do not retain raw reports, prompts, tokens, credentials, thread IDs, or
private diagnostic logs.

## Documentation and evidence handoff

Before release, re-read [README.md](../README.md),
[SECURITY.md](../SECURITY.md), all affected project/feature documentation, the
manifest, profiles, authoritative skills, schemas, and executable package
configuration. Verify links, Mermaid syntax, version strings, tool names,
storage paths, model/effort rules, commands, and the V11 compatibility boundary.
Also verify explicit root only on `create_task`, `task_id` on each other tool,
the exact task/result language fields, textual delegation scope, exact
`profile_name`/human `role`, loaded proof, one-to-one native dispatch,
model/effort, English-only complete child transcripts, chunked report modes and bounded complete-chunk reads, plan-review
and user-decision subject/digest semantics, compact paginated inspections,
required closure subject fields, worker-owned documentation-impact evidence and
initiative/closure citations, verified host-private human-view links with
localized summaries and nonblocking fallback, the bounded knowledge-routing
exception, the coordinator-only boundary, and the conditional documentation
stage.
Also verify that maintenance remains outside MCP, uses task/shard-derived
private paths and exact confirmations, preserves canonical data during cleanup,
and describes restore as strictly offline. See
[operator maintenance](features/operator-maintenance/index.md).

The final release report must distinguish:

- source checks and exact commands run;
- package-candidate checks;
- installed synchronization/check evidence;
- ordinary interactive tmux `codex` smoke evidence;
- unrun release gates or environment limitations;
- material residual risks.

Do not call the release ready while an authoritative source or public document
still describes V11 control-plane behavior as active V12 behavior.
