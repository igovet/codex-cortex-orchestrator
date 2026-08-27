# Verification index

<!-- GENERATED:START -->

This page describes Cortex 12.0.0 source, package, installed-host, and
interactive verification. A command is evidence only when it was actually run.
Do not infer installed or live-model behavior from a source-only result.

## Fast source checks

Run the structural skill/profile contract lint and the isolated
release/protocol gate:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/cortex-prompt-lint.py
PYTHONDONTWRITEBYTECODE=1 python3 -B -m pytest -q tests/test_marketplace_release_gate.py
git diff --check
```

The release/protocol test builds the explicit source candidate, compiles the
bundled Python, launches the actual MCP server under isolated temporary
projects and `HOME`, and exercises storage and public operations.

Its syntax check is in-memory (`ast.parse` and `compile`); never substitute
`py_compile` or `compileall` for source validation, because those modules write
bytecode even when `-B` and `PYTHONDONTWRITEBYTECODE=1` are set. The
source-candidate validator rejects documented validation commands that invoke
either module.

## Protocol acceptance

The V12 protocol evidence must prove:

- `tools/list` exposes exactly `create_task`, `inspect_task`,
  `create_delegation`, `read_delegation`, `submit_report`, `read_reports`,
  `set_governance_mode`, `record_initiative`, `inspect_governance`, and
  `submit_governance_closure`, and `record_user_decision`;
- coordinator and worker catalogs are identical, with no audience filtering,
  capabilities, tool-name aliases, or selector branches;
- runtime validation uses the same closed input schemas advertised by the
  registry and validates successful results against each advertised
  `outputSchema`; successful calls carry matching JSON text content plus
  `structuredContent` with `isError=false`, while caller-correctable errors use
  `isError=true` bounded sanitized text only (no `structuredContent`);
- only `create_task` accepts explicit `project_root`; all other ten tools
  require `task_id`, accept no root, and use that anchor to locate and validate
  the saved project ledger; its task/result contract separately preserves exact
  `user_request_original`, `user_language`, English `objective`, contract
  version, requirements, constraints, acceptance criteria, verification plan,
  and optional arbitrary-JSON `context`; no host metadata, plugin `cwd`, or hook
  supplies the root;
- returned task IDs match the shard-addressable opaque format and resolve the
  ledger without directory scanning;
- `create_delegation.scope` is required non-empty text no longer than 65,536
  characters, object scope is rejected, and detailed execution belongs in
  `instructions`;
- `create_delegation` requires a human-readable `role`, exact packaged
  `profile_name`, and exact model/effort together; the returned native brief
  contains the saved canonical root, unchanged coordinator `instructions`,
  loaded profile proof/digest, and one exact native-dispatch payload;
- `submit_governance_closure` requires `subject_type` plus the existing task or
  initiative `subject_id`;
- `submit_report` accepts the immutable types `progress`, `result`, `synthesis`,
  and `plan`, with `informational`/`required` review policy for plans; it
  supports `single`, `begin`, sequential `append`, `finalize`, and `abort`, and
  enforces 65,536-byte single reports, 32,768-byte chunks, 256 chunks, and 8 MiB
  per report;
- interrupted report assembly resumes from manifest and `next_chunk_index`,
  rejects gaps, post-finalize/abort appends, and overwrites, and uses explicit
  supersession for a replacement;
- `read_reports` accepts 1–20 known IDs, preserves requested order, supports
  named sections, obeys the 65,536-byte content budget, returns a scope-bound
  cursor for exact resumption, and supports `max_bytes=0` metadata-only reads;
  deprecated `byte_budget` remains an equivalent compatibility alias, and
  conflicting simultaneous values are rejected;
- `inspect_task`, `read_delegation`, and `inspect_governance` bound incremental
  reads with `after_sequence` default 0 plus `limit` default 50/range 1–200,
  return `timeline`, `next_sequence`, and `has_more`, and expose only compact
  report references; `read_reports` is
  the only bounded report body/chunk reader;
- reads create no receipt and no native lifecycle evidence;
- `record_user_decision` accepts only an existing in-scope task, delegation,
  plan, report, or same-project initiative subject; preserves exact
  `response_original`, English `response_en`, `prompt_en`, language, attribution,
  and supersession; and requires/validates the exact immutable digest for plan
  and report subjects;
- only plan `decision_type=approve` requires the exact finalized plan
  revision/digest plus a current ready approval view and opaque approval handle;
  plan `request_revision` and `cancel` preserve the exact finalized plan
  digest/response without volatile view binding, so intervening non-plan events
  cannot block saving feedback; silence/unrelated text is not approval, and
  clarification, pause, revision, cancellation, and plan review remain
  coordinator-owned ordinary-chat policy, never a backend gate or
  authorization claim;
- missing closure, `not_ready`, open initiative, unfinished linked work,
  unresolved/cyclic dependency, and missing worker report do not block a new
  delegation, report, synthesis, rework, or final answer;
- mode assessments append, the latest user override remains effective across
  later model assessments, and every revision remains in audit history;
- every transition among the six initiative status values is accepted;
- unresolved/cyclic dependencies remain warnings and initiative closure can
  retain residual dependency risk;
- task and report links cannot cross the current project ledger;
- same-payload idempotent replay returns the original record and conflicting
  replay does not mutate state;
- concurrent reports, assessments, and initiative revisions commit without
  loss and timeline sequences remain ordered;
- each accepted report append emits a task-scoped `report_chunk_appended`
  timeline event; the one-time normal-open V12 backfill appends only missing
  derived task chronology with a `backfill` marker, preserves existing timeline
  rows/report bodies, refreshes affected views best effort, and warns rather
  than guessing ambiguous report-only initiative lineage;
- sanitized errors never expose task/report content;
- a frame larger than 256 KiB is drained to a sanitized parse error and a
  following `ping`/`tools/list` still succeeds;
- state/project directories use `0700`, database/WAL/SHM files use `0600`, and
  database symlinks/non-regular paths are rejected before open;
- V11 database bytes remain unchanged.

## Human-view acceptance

Verify that the canonical V12 database is the only mutable authority and that a
semantic mutation enqueues a best-effort projection job in the same transaction.
Projection failure must not reject or roll back canonical evidence. Confirm the
exact private plan/report layout:

```text
~/.codex/cortex/v12/projects/p-<hash>/
└── tasks/<task_ref>/
    ├── plans/
    │   ├── current.md
    │   └── revisions/<plan-report-id>.md
    └── reports/<report-id>.md
```

Task, decision, delegation, initiative, closure, governance, handoff, index,
and timeline records remain in SQLite and are not emitted as user-facing
Markdown. Plan and report views must be ordinary readable Markdown rather than
JSON ledger exports: use labeled headings, normal lists, and paragraphs rather
than raw nested field dumps; preserve authored Markdown verbatim with no JSON,
`<pre>`, HTML entities, or added backslash escapes.

Use the compact `task_ref` (`t_<12-hex>`) only for the task-view directory;
canonical full IDs remain in SQLite and rendered evidence. Verify lazy migration
from one exact released `tasks/<task-id>/` directory with an atomic no-replace
rename, and verify a pre-existing `tasks/<task_ref>/` fails closed without a
merge or deletion.

Also prove all of the following:

- no database, Markdown view, report, decision, projection, or project-local
  `.codex` write occurs below `project_root`;
- a view path appears only for `ready`, after containment, regular non-symlink,
  digest, and current-source-sequence verification; `stale`, `conflict`,
  `unavailable`, and `disabled` expose no path;
- direct local edits are preserved as `conflict`; materialization uses safe
  atomic write/read-back verification and a newer source sequence supersedes a
  stale job;
- plan/report views and internal durable/projection source content are English while
  `*_original` fields preserve user text; and
- coordinator publication pairs every returned ready absolute link with a
  localized evidence summary and effect/next step, while a non-ready view is
  described inline without blocking the task or final answer.

## Maintenance CLI acceptance

Verify `cortex_runtime.v12_maintenance` as a separately invoked local operator
module, not an MCP tool:

- `tools/list` remains the exact eleven-tool registry when the module is
  packaged;
- every command requires a valid retained V12 `task_id`, derives the exact
  host-private shard, and rejects a root, arbitrary filesystem target, unsafe
  mode/owner/symlink, wrong schema/migrations/metadata, or V11 state;
- `health` is read-only and reports bounded integrity, foreign-key, schema,
  migration, task-binding, WAL, and `synchronous=FULL` checks;
- `backup --confirm-action BACKUP` uses SQLite's online backup API, seals a
  whole-project-shard copy and bounded manifest below the anchored task's
  private backup tree, then validates digest, size, task count, and health;
- checkpoint accepts only `PASSIVE`, `FULL`, `RESTART`, or `TRUNCATE` plus
  `CHECKPOINT`; optimize and vacuum require `OPTIMIZE`/`VACUUM`, with health
  checks around the operation;
- projection regeneration requires `REGENERATE_PROJECTIONS`; projection prune
  defaults to dry-run and applies only with `PRUNE_PROJECTIONS`, removes only
  exact registered stale/unavailable/disabled Markdown, and preserves ready,
  conflicted, unmanaged, digest-mismatched, canonical, project, and V11 data;
- backup retention accepts 1–20 unique sealed bundle IDs, defaults to dry-run,
  applies only with `RETENTION`, validates the complete requested set before
  mutation, and never removes canonical rows;
- restore is tested only with all normal MCP processes stopped and exact
  `RESTORE`, backup ID, task ID, `p-<hash>` shard, and `MCP_STOPPED`; create and
  validate the pre-restore recovery backup, validate final health, test recovery
  failure codes, and never call the operation online; and
- every failure returns only bounded sanitized JSON with a failing exit status,
  and no command writes beneath `project_root`.

Use the exact invocations in
[operator maintenance](../features/operator-maintenance/index.md). Package and
test commands themselves must run with `PYTHONDONTWRITEBYTECODE=1` and
`python3 -B` so validation cannot create source-tree bytecode.

## Model-routing acceptance

For every supported model, verify `low`, `medium`, `high`, `xhigh`, and `max`.
Native projection must retain `fork_turns="none"` and the exact effort.

For each durable delegation, verify the returned native-dispatch payload has
the exact rendered worker message, task/delegation and input-report references,
loaded-profile proof in the surrounding worker brief, and logical model/effort.
The host call must copy its native arguments byte-for-byte exactly once. A
missing/duplicate spawn, ad-hoc message, shared worker across delegations,
`fork_turns="all"`, omitted effort, explicit Luna model, or omitted Terra/Sol
model is a failure.

- logical `gpt-5.6-luna` omits the native `model` argument;
- `gpt-5.6-terra` passes its exact model override;
- `gpt-5.6-sol` passes its exact model override;
- invalid models and efforts are rejected;
- no backend profile, governance mode, or recovery policy rewrites the pair.

Canonical recommendations in `profiles.json` are `high` for all three models.
Do not encode a one-off development team's assignment policy in the product
contract.

## Skill/profile contract acceptance

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/cortex-prompt-lint.py
```

The lint checks the self-contained bundled skills and advisory profiles. It must
reject V11 control-plane operations, model literals in role templates, backend
admission rules derived from governance, and drift in the coordinator-only,
textual-scope, knowledge-contract, model-routing, and conditional-documentation
invariants. This is static source evidence; it makes no model call and cannot
prove coordinator behavior. It must also keep the exact knowledge-route paths
and reusable six-part template solely in the orchestrator, while profiles only
consume the coordinator-supplied delegation contract. Mutation-style checks
must reject prose that grants shell/search/graph routing or project-local
artifact/state verification to the coordinator, while accepting worker-owned
checks and negative coordinator prohibitions.
They must also reject opaque ID/digest construction, coordinator-owned
`submit_report`, closure before finalized evidence, a task-subject no-doc close,
an MCP `skill://` resource read, a report-only final initiative, empty
task/result arrays, dispatch with missing/empty six-part knowledge sections,
ad-hoc or mismatched native dispatch, localized child-thread content,
self-asserted no-impact closure, and free-form role text treated as profile
proof.

## Package and source-candidate checks

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/validate-cortex-marketplace.py
PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/verify-cortex-release.py --mode source
./scripts/sync-cortex.sh --dry-run
```

These checks validate the manifest, Marketplace entry, MCP configuration,
runtime import closure, exact bundled skills and profiles, public documentation
closure, hook absence, and release metadata.
`sync-cortex.sh --dry-run` is a repository-development preview, not the public
GitHub Marketplace installation flow and not proof of an installed cache.

Before publication, verify the committed candidate:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/verify-cortex-release.py --mode head
```

## Installed-host checks

End users install through the GitHub Marketplace instructions in
[README.md](../../README.md). Repository developers may synchronize the current
checkout only after explicit user direction:

```bash
./scripts/sync-cortex.sh
./scripts/sync-cortex.sh --check
```

Verify the installed plugin version, `multi_agent_v2`, Luna default, exact
eleven-tool catalog, bundled skill/profile content, schema-v1 path, host-private
human-view behavior, and absence of lifecycle hooks. Start a new task
after any install or update.

## Interactive tmux smoke

Use an ordinary interactive Codex CLI inside tmux. Do not use `codex exec`:

```bash
tmux new-session -s cortex-v12-smoke
codex
```

After installation or source synchronization, run exactly one fresh interactive
Cortex task first. It must reach worker-verified acceptance and an advisory
`ready` closure without a coordinator boundary violation. Only after that
single-session pass may concurrent multi-session smoke begin.

Exercise several explicit `$cortex:orchestrator` tasks:

1. Minimal bounded work with concise worker verification and closure.
2. Light work revised to full after new security evidence.
3. User-selected minimal for a risky task, preserving override plus warning.
4. `not_ready` closure followed by model-owned rework.
5. A project initiative linking several tasks/reports and closing with an
   unresolved dependency disclosed as residual risk.
6. A task whose closure is absent or unavailable but still receives a complete
   final answer.
7. A material verified change followed by a documentation-sync worker and a
   separate documentation-verifier worker before closure.
8. A verified no-impact task whose existing finalized reports either contain an
   explicit English documentation-impact section with a material/no-impact
   rationale or cause one bounded evidence-synthesis worker to submit it. The
   final initiative links that exact report ID and closure evidence cites it;
   the coordinator creates no documentation edit, never calls `submit_report`,
   and never self-asserts `documentation_not_required`.
9. A task whose coordinator reads only applicable `AGENTS.md`, both knowledge
   indexes, and one task-relevant linked page, compiles all six contract parts,
   and supplies them to a worker that does not independently redo routing.
10. A deliberately missing or stale index handled by a bounded discovery
    worker without direct coordinator source inspection or inferred harvest.
11. A required plan review that waits for an explicit decision bound to the
    finalized plan digest, then creates a revised plan and new review after a
    revision response.
12. A localized non-English user decision whose every child commentary,
    inter-worker message, final response, tool-authored durable string, task,
    report, decision, and task-view source remains English with `*_original`
    text preserved; the coordinator publishes a localized summary and a
    verified ready link.
13. A large result written through `begin`/ordered `append`/`finalize`, then
    read through a section-scoped cursor and resumed without duplicate chunks.
14. A required-plan case where the user forbids coordinator project operations;
    project discovery/planning is worker-owned before the plan-review hold and
    the coordinator performs no project search.
15. A reportless/rework case where a user asks about project-local `.codex`;
    the coordinator delegates the existence/absence/unchanged check and never
    searches the project itself.
16. A live task whose four task/result arrays are all non-empty and whose every
    delegation carries the six knowledge sections exactly once, in order, with
    non-empty delegation-specific content before native spawn.
17. The no-impact route creates/updates a final initiative with both the exact
    task relationship and every required finalized report link, closes the
    exact returned initiative, and verifies the closure and links through both
    task-scoped and initiative-scoped governance. A report-only initiative,
    premature ready claim, or edited/reconstructed ID fails the scenario.
18. Explicit activation uses host-supplied skill context without any
    `read_mcp_resource`, `resources/read`, or `skill://` MCP request.
19. Four successful durable delegations produce four distinct matching native
    spawns from their returned payloads, each with `fork_turns="none"`, explicit
    selected effort, correct Luna/Terra/Sol model transport, full renderer and
    profile evidence, and a report submitted by that exact worker.
20. Ordinary delegations use exact packaged `profile_name` values and return
    loaded profile proof plus digest. The separate human-readable `role` does
    not pass as a selected profile; degraded non-durable fallback is accepted
    only with a complete explicit role contract and visible unavailable-profile
    disclosure.
21. C1, C2, and C3 are retained as advisory model baselines mapping normally to
    `minimal`, `light`, and `full`; no label creates a backend wave, mandatory
    stage, automatic model escalation, or user-approval gate.
22. When planning is needed, the coordinator creates a planner delegation and
    never writes the solution plan. The planner's finalized immutable `plan`
    report is the predecessor for plan-dependent stages.
23. A material report, decision, failed/incomplete check, changed risk,
    contradiction, scope change, or documentation finding loads
    `adaptive-pipeline`; evidence can add, remove, reorder, retry, or
    parent-link rework unstarted stages while completed evidence remains
    immutable.

Confirm native Luna dispatch omits the model override while Terra/Sol carry
exact overrides and every selected effort is preserved. Confirm no hook trust,
native stop barrier, backend-enforced fixed wait/read sequence, or server
recovery route appears; the coordinator must still wait for or reconcile its
exact spawned worker before consuming that worker's finalized report.

Inspect the coordinator chronology. Its only non-user actions may be Cortex
ledger calls, native worker coordination, and the bounded orchestrator-owned
knowledge route. It must read applicable `AGENTS.md`, the two knowledge indexes,
and only task-relevant pages selected from them through non-shell direct reads
of already-known exact paths. It must not use shell, `rg`, `find`, globs,
graph/source/repository search, directory listing, or candidate probes for that
route. It must not perform root discovery or any project-state/artifact check,
including Git, manifests, caches, worktrees, existence/absence,
unchanged-state, or project-local `.codex`. Project verification must be
performed by a delegated worker. After the verification report, the
documentation-impact decision must precede closure. Missing documentation
update or verification evidence may cause model-owned rework/replacement or
explicit risk disclosure, never a backend lifecycle gate.

## Documentation review

Re-read README, SECURITY, release readiness, every affected project/feature
page, the manifest, authoritative skills, profiles, and executable package
configuration. Check:

- links and anchors;
- Mermaid syntax and visual completeness;
- V12/12.0.0/schema-v1 identifiers;
- exact eleven-tool names;
- explicit `project_root` only on `create_task`, `task_id` on every other tool,
  exact task/result contract fields, arbitrary optional task `context`, textual
  delegation scope, exact model/effort, compact paginated inspections, report
  bodies/chunks only through bounded `read_reports`, and required closure subject
  fields;
- report types, plan review policies, chunked report modes/limits/resume, and
  exact user-decision subject/digest/original-language semantics;
- storage paths;
- model/effort and Luna omission rules;
- exact packaged `profile_name` versus human-readable `role`, loaded renderer
  proof, and one-to-one byte-exact native dispatch;
- GitHub Marketplace user installation versus repository source sync;
- the packaged coordinator-communication policy: result → impact → next step,
  latest meaningful user language, silent unchanged waits, default hidden
  internals, progressive detail, English durable-worker content, and safe
  optional humor without any A/B or model-quality claim;
- historical V11 mentions only at the untouched/incompatible boundary;
- absence of active lifecycle telemetry, hooks, waves, gates, receipts,
  capabilities, and server recovery claims;
- coordinator-only orchestration, worker-owned project action/analysis, and the
  conditional report-grounded documentation stage before closure;
- opaque byte-for-byte IDs/digests/cursors, worker-only `submit_report`, exact
  task-versus-initiative closure fields, and ready claims only after closure
  write plus scoped governance inspection;
- complete English-only child transcripts and a finalized worker-owned
  documentation-impact report linked in the final initiative and cited in
  closure evidence;
- single-authority bounded knowledge routing, complete non-empty
  per-delegation six-part contracts, non-empty task/result arrays, and profiles
  that consume rather than reconstruct routing;
- canonical host-private database ownership, zero `project_root` projection
  writes, exact human-view layout/statuses, and verified ready-link publication
  with localized summaries. See
  [human-readable task views](../features/human-readable-task-views/index.md).

Record every exact command, outcome, environment, skipped scenario, and material
limitation. Never claim an unavailable host or live-model check passed.

<!-- GENERATED:END -->
