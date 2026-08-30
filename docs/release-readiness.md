# Release readiness

Status: source-mode release contract for Cortex 1.12.1.

## Current release identity

- release label: 1.12.1
- coordination contract: V12 durable, nonblocking ledger
- SQLite schema: v1 in the new V12 namespace
- public facade: exactly fifteen action-specific MCP tools
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

1. `open_task`
2. `read_task`
3. `open_clarification`
4. `record_clarification`
5. `open_plan_review`
6. `record_plan_review`
7. `open_steering`
8. `record_steering`
9. `open_assignment`
10. `consume_assignment_evidence`
11. `publish_plan`
12. `publish_result`
13. `publish_documentation`
14. `assess_governance`
15. `close_task`

The catalog is identical for every participant. There is no audience filter,
capability matrix, host-bound authority, tool-name alias, or action selector. Each input
schema is closed and is also the runtime validator's source; every tool also
advertises its successful `outputSchema`, which runtime validates before a
success is transported as JSON text plus `structuredContent` with
`isError=false`. Caller-correctable errors are bounded sanitized text-only
`isError=true` results with no `structuredContent`; server-state failures are
sanitized JSON-RPC internal errors.

When the complete catalog would exceed the 256 KiB physical JSONL frame bound,
the standard `tools/list` response returns complete definitions on opaque
`nextCursor` continuation pages. Clients follow the cursor until absent to
recover the unchanged ordered fifteen-tool semantic catalog; a definition is never split
or truncated to make a frame fit.

Only `create_task` accepts the exact resolved `project_root` and stores the
canonical project association; it is the sole public project-root boundary. It
returns compact `task_ref` for the seven task-anchored tools. The durable
`task_id` in results is non-callable evidence. `read_delegation` instead uses
`delegation_ref`; `submit_report` uses `delegation_ref` and continuation
`report_ref`; and `read_reports` uses `report_refs`, resolving their task from
those exact compact refs. Initiative calls use `task_ref` only as a locator,
never as permission. The native worker brief carries the saved root only for
working-directory context. No root is inferred from MCP metadata, thread
identity, the plugin process `cwd`, or a lifecycle hook.

`create_task` is an exact, versioned task/result contract. It keeps the exact
arbitrary-Unicode `user_request_original` and `user_language` beside the
English-normalized internal `objective`, English bounded `requirements`,
`constraints`, and `acceptance_criteria`; a verification plan is deterministically
derived and persisted from acceptance criteria, plus
`task_contract_version` and optional bounded JSON `context`. The English
normalization does not replace the original request, and the result contract is
not a backend execution or permission gate. `context` never supplies or
overrides the root. `create_delegation.scope` is required non-empty text
(maximum 65,536 characters) describing the concise worker-ownership boundary;
detailed execution belongs in `instructions`, and object-shaped scope is
invalid. `create_delegation` also separates exact packaged `profile_name` from
the human-readable `role`, requires loaded renderer proof, and returns a
host-neutral `dispatch_brief`. Codex maps that semantic brief to one matching
active host spawn; Cortex does not prescribe static host arguments or lifecycle
behavior. `read_delegation` retains the verbose brief and bounded chronology for
recovery and is not required on the healthy path.
The three narrow decision record operations use their matching server-issued
binding and closed advertised contract; the server owns the task and
subject refs, decision type, neutral `prompt`, exact original response, and user
language; `subject_digest` is required only
for plan/report subjects. Plan approval additionally
requires the matching ready-view handle, view digest, and source sequence from
one returned relation; missing, renamed, extra, or cross-mixed fields fail
before mutation.
`submit_governance_closure` requires `subject_type` and the existing compact
task or initiative `subject_ref`; durable `subject_id` is evidence only.

The root coordinator may use the ledger, user interaction, native worker
coordination, and worker reports to orchestrate and synthesize. It must never
inspect or search source/code/configuration, create or edit target-project
files, perform substantive domain analysis, or run project commands, builds,
tests, browser checks, or direct verification. Every such action is
worker-owned. Before project delegation, its only project-read exception must
follow the bounded route through the host-injected `AGENTS.md` context, the project and feature
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
contain the complete ordered additive migration history through
`v12-effective-outcome-coverage`, and retain matching project-hash metadata. The schema contains
tasks, delegations, immutable reports and report
chunks, append-only governance assessments, user decisions that preserve exact
original text and neutral prompt, initiatives, append-only initiative
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
  supports assembled `begin`, sequential `append`,
  `finalize`, and `abort` writes, with immutable
  sequential chunks, terminal finalization/abort, and explicit supersession;
- each chunk is bounded to 32 KiB; report assembly is bounded to 256 chunks and
  8 MiB; its final digest binds the complete immutable manifest;
- v1 reports remain readable, while additive v2 result, synthesis, and plan
  reports retain structured contract coverage, deviations, unresolved items,
  risks, and verification;
- the current V3 specialist planner envelope is pre-terminally admitted against
  the full current effective contract, independent of delivery assignments:
  stable planner tokens map every current item once and ordered stages carry an
  owner, earlier dependencies, work, and verification; a correctable mapping
  failure remains in the same immutable assembly for corrective append rather
  than creating a terminal semantic-invalid plan or another planner delegation;
- `read_reports` preserves requested order for 1–20 known compact `report_refs`, returns only
  complete chunks within a 65,536-byte budget, and returns a selection-scoped
  cursor without duplication; metadata-only reads omit a consuming delegation;
- inspection pages use `after_sequence` with a fixed server-side page of 50, return stable
  `next_sequence`/`has_more`, and expose compact report references while
  `read_reports` remains the only bounded report body/chunk reader;
- effective-contract item references remain stable across requirements,
  constraints, acceptance criteria, and verification expectations; current
  ownership is non-overlapping, finalized report coverage detects missing,
  partial, unverified, stale, and contradictory evidence, and user steering
  revises only affected items;
- advisory conformance review relates the active effective-contract revision,
  user decisions, finalized report manifests, completed coordinator-read
  digests, and aggregate coverage without becoming a lifecycle gate;
- task/report/initiative links do not cross project ledgers;
- errors never expose raw task or report content;
- state/project directories are `0700`, database/WAL/SHM files are `0600`, and
  symlink or non-regular database paths are rejected before SQLite opens;
- an oversized stdio frame is drained with a sanitized error and does not
  prevent a following `ping` or `tools/list` request;
- SQLite integrity checks pass after concurrent operations.

## User decisions, plan review, and human views

The matching narrow decision record operation records an ordinary-chat decision only when the
coordinator asserts that the user made one. The request selects the subject
with compact `subject_ref`; the returned durable `subject_id` is evidence only.
The record keeps subject type and
a subject digest when applicable, a decision type, neutral `prompt`, exact
arbitrary-Unicode `response_original`, user language, attribution, and optional
supersession. Plan and report
decisions require the exact immutable digest; a plan decision also requires a
completed, finalized `plan` report. Only plan `approve` additionally requires a
current ready approval view and opaque approval handle. Plan `request_revision`
and `cancel` preserve the exact finalized plan digest and response without
volatile view binding, so intervening non-plan timeline events cannot block
feedback. The record binds evidence and scope but is not authentication, a
bearer approval token, or a backend lifecycle gate.

Each narrow decision record operation has one closed canonical contract. Retired
`prompt_en` and `response_en` aliases, partial inputs, and mixed shapes are
rejected by the public schema before mutation.

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
durable delegation returns one host-neutral `dispatch_brief` with semantic
fields that Codex maps to exactly one matching active host spawn; the ledger
does not prescribe static host argument names or lifecycle. No ad-hoc prompt,
shared worker, missing spawn, or duplicate spawn is acceptable.

## Operator maintenance contract

The packaged `cortex_runtime.v12_maintenance` module remains outside
`tools/list`; it cannot change the fifteen-tool semantic catalog. Every command in this
separately invoked non-MCP operator module starts from one exact V12 durable
`task_id`, derives its shard and host-private targets from that ID, accepts no
root/arbitrary path/V11 target, validates the owner-only
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
fifteen-tool semantic facade and runtime, schema-v1 store, host-private operator
maintenance module, advisory profiles, bundled skills, direct MCP configuration,
and assets. It must not ship lifecycle hooks or lifecycle hook code.

The package and repository metadata must consistently identify Cortex 1.12.1,
schema v1, the nonblocking ledger, model-owned governance, advisory profiles,
and the exact fifteen-tool semantic catalog. Stale claims about waves, gates, capabilities,
plan authority, host epochs, receipt-gated lifecycle, required wait/read order,
lifecycle HMAC, repair escrow, closure breakers, resource locks, required
governance workers, or server-owned recovery are release defects. Worker
handoff delivery receipts are valid evidence but must not be described as host
lifecycle authority or proof of physical worktree/workspace isolation.

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
links its exact compact `report_ref` in the final initiative, and cites that ref
plus its returned digest in closure evidence. Durable `report_id` is evidence
only. A self-asserted
`documentation_not_required` value is invalid. This
conditional stage precedes advisory closure and the final answer. Missing
documentation evidence leads to model-owned rework, replacement, or explicit
risk disclosure rather than a backend lifecycle gate.

Every source check is evidence only when actually run. A source candidate does
not prove an installed plugin, active Codex configuration, native subagent
behavior, or an interactive user flow.

## Installed and interactive verification

End users install or update through the README's GitHub Marketplace flow. For
an explicitly authorized interactive local-source synchronization from this
checkout, repository developers use the isolated candidate helper:

```bash
./scripts/cortex-dev
```

It keeps the stable HOME/CODEX_HOME and V12 state outside the candidate by
using the exact persistent `$HOME/.cortex-dev` directory. Reset is explicit and
path-guarded:

```bash
./scripts/cortex-dev-reset --confirm
```

Direct `./scripts/sync-cortex.sh` remains the explicitly authorized
source-checkout operation; run `--check` in the environment whose candidate or
installation is being checked.

## Interactive tmux live-dev gate

The release gate uses a real, user-visible ordinary Codex session. `./scripts/cortex-dev` refreshes the isolated candidate but does not create tmux; `./scripts/cortex-live-smoke start` creates the exact default-server session with an ordinary `bash` pane, attaches an owner-only output-only `pipe-pane` observer to that exact pane, and only then inserts the fixed launcher command literally and submits it with one standalone Enter. The launcher prints `Cortex live-dev exit=<status>` and exits with that same status.

```bash
./scripts/cortex-live-smoke start
./scripts/cortex-live-smoke status
./scripts/cortex-live-smoke capture
./scripts/cortex-live-smoke events
TERM=xterm-256color tmux -f /dev/null attach -t cortex-v12-smoke
# Only after visibly observing a fresh-project trust screen:
./scripts/cortex-live-smoke enter
./scripts/cortex-live-smoke send --prompt-file TASK_PROMPT.txt
./scripts/cortex-live-smoke capture
./scripts/cortex-live-smoke stop
```

After `start`, `capture` reads the bounded output-only PTY stream when detached `capture-pane` is stale. `events` reads the exact session's bounded owner-only sanitized MCP observation stream. It exposes safe metadata only and never parses readiness, errors, replay, or acceptance; the LLM verifier owns those decisions. Visibly confirm the Codex state in `attach` or `capture` before any input; `pane_current_command=codex` alone is insufficient because early text or submission can be lost during TUI initialization. If the visibly observed fresh-project trust screen asks for acknowledgement, the operator/LLM may use `enter` exactly once; it sends one standalone Enter to the exact pane, does not auto-trust a directory, and does not edit Codex trust configuration. Then visibly confirm the composer before `send`. Each task must provide its own prompt, identify the changed behavior, state that the session is already live-dev, and forbid nested tmux, cortex-dev, shell validation, and repository inspection. The transport uses safe unframed tmux-buffer insertion, then computes collapsed paste blocks as `ceil(normalized Unicode character count / 1024)` for current Codex 0.149.1 compatibility. It waits five seconds before each standalone `C-m`: one per collapsed paste block, plus one final key requesting submission; its receipt reports counts and key deliveries only, never TUI acceptance. The coordinator/LLM decides readiness, rollout, acceptance, and errors from the terminal and bounded events. Observe actual task-relevant Cortex MCP calls. `Cortex tool error`, `validation_error`, `schema_unsupported`, traceback, or a missing marker is a failed gate. A repeated successful mutation without an explicitly ambiguous prior transport result is also a failed gate; backend idempotency does not excuse an unexplained replay. The stabilization example requires exactly one task-creation request and a non-replayed success before its sentinel. Use the default tmux server, isolated HOME/CODEX_HOME, ordinary Codex, bounded captures, and exact-session cleanup only; never use `codex exec`, another socket, or stable plugin updates.

The current prompt transport contract is literal insertion with one `send-keys -l`, a real five-second wait after insertion returns, and exactly one standalone named `Enter`; no pre-submit `C-m` or `C-j` is permitted. The transport reports delivery only; the LLM verifier owns TUI acceptance.

For every native worker spawned by live orchestration, the LLM verifier must inspect a bounded sanitized structured event stream as well as the coordinator pane because worker MCP calls/errors may be hidden. The helper may expose events but must not decide pass/fail. Acceptance requires a clean first worker-owned report-submission success, zero prior hidden validation/tool errors or mutation replays; a final report reference alone is insufficient.

The E2E acceptance case is multi-turn and runs in a separate test project. The LLM observes the pane, answers exactly one product clarification with the predefined safe answer, later approves the visibly rendered plan, and follows planner → implementation → independent verification → documentation-impact assessment → closure. It inspects every native worker event stream and fails on any hidden tool error or unexplained replay. The tmux transport never answers or approves autonomously.

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
    plus all other required reports, closure evidence cites their exact compact
    refs (durable IDs remain non-callable evidence) and
    returned digests, and both task-scoped and initiative-scoped governance
    verify the closure before any durable-ready claim.
14. An explicit activation with no `read_mcp_resource`, `resources/read`, or
    `skill://` MCP attempt, plus exact byte-for-byte reuse of every returned
    compact ref, durable evidence ID, and digest in the appropriate context.
15. Four durable delegations produce four matching host spawns mapped exactly
    once from their returned host-neutral `dispatch_brief` values, with the
    required `fork_turns="none"`, model/effort semantics, renderer/profile
    evidence, and worker-owned reports. Physical worktree/workspace isolation is
    not asserted here: the semantic brief has no host workspace selector and
    that capability remains unconfirmed outside the ledger.
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
    compact ref/digest citations in closure evidence (durable IDs remain
    non-callable evidence). Reject coordinator self-assertion.
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
Also verify explicit root only on `create_task`, compact `task_ref` on the seven
task-anchored tools, compact `delegation_ref`/`report_ref`/`report_refs` on
entity-derived tools, `subject_ref`/`initiative_ref` where applicable, the exact
task/result language fields, canonical report schemas and one optional
unchanged `source_text` value (without language or translated/original
duplicates), evidence-only planner microtasks, textual delegation scope, exact
`profile_name`/human `role`, loaded proof, one-to-one native dispatch,
model/effort, English-only worker-authored content, chunked report modes and bounded complete-chunk reads, plan-review
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
Current live transport submission is one literal normalized insertion, a five-second wait, and exactly one standalone named `Enter` key to the same exact pane. Receipts report delivery only; the coordinator/LLM confirms TUI acceptance from the pane and bounded events.
