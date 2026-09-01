# Verification index

<!-- GENERATED:START -->

This page describes Cortex 1.14.9 source, package, installed-host, and
interactive verification. A command is evidence only when it was actually run.
Do not infer installed or live-model behavior from a source-only result.

## Fast source checks

Run the structural skill/profile contract lint and the isolated
release/protocol gate:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/cortex-prompt-lint.py
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=plugins/cortex/scripts python3 -B -m pytest -q
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

- the complete registry contains exactly the fourteen current operations: `open_task`,
  `read_task`, `open_assignment`, `publish_plan`, `publish_result`,
  `publish_documentation`, `assess_governance`, `close_task`, and the six
  narrow clarification, plan-review, and steering open/record operations;
- coordinator `tools/list` excludes all worker publications; signed
  worker-candidate/worker `tools/list` contains exactly `read_task` and the
  three publications and excludes assignment creation, governance, decisions,
  and closure;
- runtime validation uses the same closed input schemas advertised by the
  registry and validates successful results against each complete private
  runtime result schema; the advertised `outputSchema` is a compact public
  projection limited to essential handles, lifecycle states, replay/continuation,
  and next-action data. Each tool description mechanically lists the exact
  required input properties derived from its advertised `inputSchema` and tells
  the caller to verify them before invocation. Successful calls carry matching
  JSON text content plus `structuredContent` with `isError=false`, while
  caller-correctable errors use `isError=true` bounded sanitized text plus a
  matching sanitized `structuredContent.error`; when several required fields
  are absent, its `details.missing_fields` and recovery action report the full
  bounded list in one response;
- only `open_task` accepts explicit `project_root`; task-anchored
  tools use the returned compact `task_ref`, while `open_assignment` returns
  the compact server-rendered worker bootstrap and `publish_*` uses the
  assignment continuation; the worker's mandatory first assignment read then
  supplies full common policy, profile guidance, and task evidence; no separate
  report-read operation exists
  references. No public tool accepts a durable `task_id` or another direct-ID
  alternate; the task/result contract separately preserves exact
  `user_request_original`, `user_language`, English `objective`, contract
  version, independent outcomes, their linked acceptance criteria, constraints,
  the non-derived verification plan, and optional arbitrary-JSON `context`; no
  host metadata, plugin `cwd`, or hook
  supplies the root;
- returned `task_ref` values match the compact locator format and resolve the
  ledger without directory scanning; durable task IDs remain non-callable
  evidence;
- `open_assignment.scope` is required non-empty text no longer than 65,536
  characters, object scope is rejected, and detailed execution belongs in
  `instructions`;
- `open_assignment` requires a human-readable `role`, exact packaged
  `profile_name`, and exact model/effort together; its successful response is
  one compact closed native dispatch plus replay state; it preserves exact
  effort, omits the model only for default Luna, and is forwarded unchanged to
  one active host spawn. The worker's first task read is
  the server-rendered `read_task` assignment view, which supplies full common
  policy, profile guidance, and bounded task evidence on healthy and recovery
  paths;
- `open_clarification` and `record_clarification` record an ordinary direct
  product/requirement answer and are distinct from closure review;
- after presenting the current result, closure review renders exactly two
  localized choices: revise the same task or close it. Revision preserves the
  same `task_ref`; a later assignment, report, or decision stales any earlier
  close choice;
- the public `close_task` path atomically requires the current consumed close
  choice and rejects missing, reused, or stale choices. Internal advisory
  storage may remain policy-neutral; this public boundary does not schedule or
  gate safe work;
- `close_task` requires the exact task reference, one advisory verdict, and
  bounded closure evidence; durable task IDs are evidence only;
- after sufficient finalized worker evidence, coordinator policy selects only
  `ready`, `ready_with_risks`, or `not_ready`, automatically attempts the
  advisory write, and performs bounded inspection of the intended record;
  `ready_with_risks` does not request user confirmation;
- `read_task` exposes independent `execution_outcome` and
  `advisory_closure` projections. `execution_outcome` contains
  `evidence_status`, `finalized_report_count`, `completed_report_count`,
  `effective_revision`, `coverage_status`, and `outcome`; its result derives
  deterministically from current effective-contract coverage and makes no
  native-lifecycle claim;
  `advisory_closure` contains `record_status` and `latest_record` (or `null`);
  a closure record cannot turn report evidence into a completion claim;
- `close_task` returns `closure_confirmation` with
  `inspection_status`, `reason`, and `attempts` (1 or 2), and retries at most
  once through server-owned replay reconciliation for a verified transient persistence or
  inspection failure. An `unconfirmed` result preserves and reports the
  independent `execution_outcome` evidence;
- `publish_plan`, `publish_result`, and `publish_documentation` accept the
  corresponding immutable worker evidence. `publish_plan` requires an explicit
  `informational` or `required` review policy; publication is atomic at the
  semantic operation boundary;
- private/internal publication bodies support the fixed `cortex/report/{progress,result,synthesis,plan}/v1`
  schemas plus additive `cortex/report/{result,synthesis,plan}/v2` schemas;
  v2 coverage names only effective-contract items assigned to the reporting
  delegation and carries deviations, unresolved items, risks, and verification.
  One optional unchanged `source_text` value is allowed, with no language tag or
  translated/original duplicate; storage-valid legacy and semantic-invalid
  bodies remain evidence, while only completed semantic-valid canonical plans
  receive ready approval relations;
- the current V3 specialist planner envelope is admitted before terminal
  finalization: its stable planner brief tokens cover the full effective
  contract exactly once, stages have explicit ordered ownership/dependencies/
  work/verification, and an incomplete mapping stays assembling for a
  corrective immutable append rather than producing a terminal semantic-invalid
  plan or requiring a second planner delegation;
- every advertised input schema exposes the complete compact UTF-8 aggregate
  bound. Verify below, exact, and above-bound payloads, multibyte Unicode,
  dominant and aggregate sections, value-blind actual/maximum diagnostics, no
  invented root field, no irrelevant handle guidance, one complete materially
  corrected `publish_result`, unchanged/incomplete/second-attempt rejection,
  and separation from domain, report, and physical-frame failures;
- worker bootstrap returns an ordered pre-publication reconciliation template
  for the exact immutable assignment scope; verify its count, exact public
  outcome names, and
  disposition-free rows agree with the planner or assigned item collection and
  are preserved through a clean first publication call;
- worker assignment authority and predecessor evidence paginate with private
  ordered positions and digest-only receipts; exact page restart is
  non-mutating, skipped/stale continuations fail closed, UTF-8 fragmentation is
  lossless at the byte boundary, and publication is unavailable until the
  terminal page;
- real persistent source-stdio tests prove the original host-bound connection
  can consume and publish once, a copied locator on another initialized process
  cannot read or publish, a coordinator connection cannot switch to worker
  audience, and no failed copy creates a report operation;
- lost-worker tests require explicit blocked/aborted reason and non-empty
  evidence, atomically stale the old lease and link one successor, reject
  unrecorded consumed or expired leases, and preserve immutable evidence;
- `read_task` exposes the revisioned effective contract and aggregate
  coverage. Verify one stable active item per independent user outcome, linked
  acceptance/verification without duplicates, exact source fragments, one current owner per item,
  allowed contributing/evidence-producing roles, completed/partial/unverified/
  stale/contradictory coverage classification, independent unpaired steering
  additions, an atomic one-retire/one-add replacement, and preservation of
  unaffected coverage;
- advisory conformance evidence links the current contract revision, decisions,
  finalized report manifest digests, completed coordinator-report consumption,
  and aggregate coverage without becoming a dispatch, reporting, or closure
  admission gate;
- interrupted private/internal publication assembly retries the same server-bound
  append payload; it rejects post-finalize/abort appends and overwrites, and uses explicit
  supersession for a replacement;
- `read_task` returns bounded server-produced task state, assignment, or evidence
  data; workers start with the assignment view and resume only with its
  server-owned continuation;
- large assignment tests verify the exact publication outcome appears in the
  compact reconciliation header, the structured response is not duplicated in
  text, continuation follows only `has_more=true`, and a restarted terminal
  read reuses rather than duplicates its consumption receipt;
- report-read request/response aggregation is preflighted before body
  materialization (including report/chunk counts and the 224 KiB response cap),
  and projection rendering preflights its aggregate 512-file/32 MiB output
  budget plus the 10 MiB per-file cap without partial writes;
- `read_task` bounds task reads and returns the advertised `has_more` continuation
  state; no obsolete inspection or report-body operation is public;
- ordinary `read_task` reads create no native lifecycle evidence;
- The narrow decision record operations accept an existing in-scope task and
  their task-ref-only advertised fields; private subject/revision/digest
  bindings remain server-owned. They preserve attribution and supersession;
- only plan review approval requires a current private ready plan relation;
  missing, renamed, extra, or cross-mixed public fields fail before mutation;
  plan `request_revision` and `cancel` preserve the exact finalized plan
  digest/response without volatile view binding, so intervening non-plan events
  cannot block saving feedback; silence/unrelated text is not approval, and
  clarification, pause, revision, cancellation, and plan review remain
  coordinator-owned ordinary-chat policy, never a backend gate or
  authorization claim;
- missing closure, `not_ready`, private/internal initiative status, unfinished linked work,
  unresolved/cyclic dependency, and missing worker report do not block a new
  delegation, report, synthesis, rework, or final answer;
- mode assessments append, the latest user override remains effective across
  later model assessments, and every revision remains in audit history;
- every transition among the six initiative status values is accepted;
- unresolved/cyclic dependencies remain warnings and private/internal initiative
  closure bookkeeping can retain residual dependency risk;
- task and private/internal publication links cannot cross the current project ledger;
- same-payload server-owned replay returns the original public receipt and conflicting
  replay does not mutate state;
- concurrent reports, assessments, and initiative revisions commit without
  loss and timeline sequences remain ordered;
- each accepted private/internal publication append emits a task-scoped `report_chunk_appended`
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

The report-ID placeholders above name host-private storage files; they are not
public MCP inputs. Worker publication operations use the worker-scoped
`task_ref`; private assignment, continuation, and report IDs remain
ledger/evidence fields.

Task, decision, delegation, initiative, closure, governance, handoff, index,
and timeline records remain in SQLite and are not emitted as user-facing
Markdown. Plan and report views must be ordinary readable Markdown rather than
JSON ledger exports: use renderer-owned labeled headings, normal lists, and
paragraphs rather than raw nested field dumps. Treat authored strings as data
and sanitize them context-sensitively so headings, lists, tables, blockquotes,
HTML, rules, and fences cannot inject structure while ordinary punctuation stays
readable. Only explicitly typed blocks (such as code blocks) emit intended
formatting. The optional `cortex/report-view/v1` envelope is render-time only;
malformed, unknown, and legacy content must use the safe generic fallback and
must not change report submission or persistence. Do not emit JSON, `<pre>`,
or opaque serialized payloads in views.

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
- plan/report views and worker-authored durable/projection source content are
  English; canonical product-facing report/handoff payloads may carry one
  optional unchanged `source_text` value without language tags or
  translated/original duplicates, while existing task/decision `*_original`
  fields preserve user text; and
- coordinator publication pairs every returned ready absolute link with a
  localized evidence summary and effect/next step, while a non-ready view is
  described inline without blocking the task or final answer.

## Maintenance CLI acceptance

Verify `cortex_runtime.v12_maintenance` as a separately invoked local operator
module, not an MCP tool:

- the complete registry remains fourteen tools and both audience projections
  remain exact when the module is packaged;
- every command in this separately invoked non-MCP operator module requires a
  valid retained V12 durable `task_id`, derives the exact host-private shard,
  and rejects a root, arbitrary filesystem target, unsafe
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

For each durable delegation, verify the successful `open_assignment` response
has one compact closed native dispatch plus replay state, preserves exact
effort, and omits the model only for default Luna. The active host forwards the projection exactly once to
its spawn operation, followed by the required first assignment read. A
missing/duplicate spawn, ad-hoc message, shared worker across delegations, or
model/effort mismatch is a failure.
- invalid models and efforts are rejected;
- no backend profile, governance mode, or recovery policy rewrites the pair.

Canonical recommendations in `profiles.json` are `high` for all three models.
The product-level routing policy is Luna-first: Explorer always uses Luna,
Terra requires evidence of genuinely complex non-security work or planning,
and Sol is reserved for security. Raise Luna effort before changing models; do
not encode an automatic escalation ladder.

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
worker-only publication, closure before finalized evidence, a task-subject no-doc close,
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
closure, bundled hook contracts, and release metadata. The installable manifest
must be `1.14.9+codex.sha256.<digest-prefix>` and its suffix must match the
normalized plugin payload. Validation also rejects a `defaultPrompt` over 128
UTF-8 bytes or a `SessionEnd` timeout over three seconds.
`sync-cortex.sh --dry-run` is a repository-development preview, not the public
GitHub Marketplace installation flow and not proof of an installed cache.
Normal `sync-cortex.sh` mode removes only disposable bytecode under the packaged
plugin before validation and regenerates the marked orchestrator model-routing
table from `profiles.json`; `--dry-run` and `--check` remain read-only.
During install, the checkout's generated content-address suffix is a source
template and may be stale after a working-tree edit; the workflow rebuilds and
validates the immutable stamped candidate before marketplace registration.
Read-only `--check` continues to reject a stale checkout suffix.

The GitHub release gate installs both `pytest` and `PyYAML` explicitly before
running the validators. This matters because marketplace validation imports
YAML support before the test suite starts; a clean runner must not depend on a
transitive or preinstalled `yaml` module.

Before publication, verify the committed candidate:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/verify-cortex-release.py --mode head
```

## Installed-host checks

End users install through the GitHub Marketplace instructions in
[README.md](../../README.md). For every interactive repository live-dev test,
use the isolated helper before ordinary Codex so the stable HOME/CODEX_HOME and
V12 state remain untouched and the candidate cache/version is refreshed first:

```bash
./scripts/cortex-dev
```

The helper prints and then synchronizes the checkout under the exact persistent
`$HOME/.cortex-dev` candidate runtime (`CODEX_HOME=$HOME/.cortex-dev/.codex`),
then launches ordinary Codex. Its
companion reset requires `./scripts/cortex-dev-reset --confirm` and refuses
stable, repository, broad, symlinked, or non-regular targets. Direct
`./scripts/sync-cortex.sh` is never a live-dev mechanism; `--check` remains
read-only and must be run in the environment whose candidate is being checked.
Never install, reinstall, update, or synchronize the user's real installed
plugin for repository live development.

Verify the installed plugin version, `multi_agent_v2`, Luna default, exact
fourteen-tool catalog, bundled skill/profile content, schema-v1 path, host-private
human-view behavior, content-addressed runtime identity, and bounded lifecycle
hooks. A production stdio smoke must omit `CORTEX_SOURCE_MODE`, receive a
successful `initialize`, report semantic version `1.14.9` with
`runtimeMode=content_addressed`, and expose the full tool catalogue. Start a new task
after any install or update.

## Interactive tmux live-dev workflow

visibly confirm that the interactive composer is rendered before sending.

Before submitting a workload, the LLM/operator must observe the passive
host-owned activation receipt for the exact isolated candidate. Candidate
identity, registered Cortex server identity, and advertised catalogue identity
must agree. This is observation-only: transport exposes it, while the
coordinator/LLM verifies it; absence makes the environment unverified. Once
`cortex:orchestrator` is selected, the first project execution action must be
the catalogued `open_task` operation. Prose activation acknowledgement,
shell/repository inspection, project-state checks, or worker dispatch before
task opening is a route violation.

Use the real operator-controlled ordinary Codex session. `./scripts/cortex-dev` refreshes the isolated candidate but does not create tmux; `./scripts/cortex-live-smoke start` creates the exact session on the default server with an ordinary `bash` pane, attaches an owner-only output-only `pipe-pane` stream to that exact pane, and only then inserts the fixed launcher command literally and submits it with one standalone Enter. The launcher prints `Cortex live-dev exit=<status>` and exits with that same status.

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

After `start`, `capture` reads the bounded output-only PTY stream when detached `capture-pane` is stale. `events` reads the exact session's bounded owner-only sanitized MCP observation stream. It exposes safe metadata only and never parses readiness, errors, replay, or acceptance; the LLM verifier makes those decisions. Visibly confirm the Codex state in `attach` or `capture` before any input; `pane_current_command=codex` alone is insufficient because early text or submission can be lost during TUI initialization. If the visibly observed fresh-project trust screen asks for acknowledgement, the operator/LLM may use `enter` exactly once; it sends one standalone Enter to the exact pane, does not auto-trust a directory, and does not edit Codex trust configuration. Then visibly confirm the composer before `send`. Every task authors its own prompt for its changed behavior. It must say the session is already live-dev and prohibit nested tmux, cortex-dev, shell validation, and repository inspection. The controller normalizes the prompt to one line, uses literal `send-keys -l`, then issues one separate `send-keys ... Enter` call; it does not poll readiness or decide acceptance. Observe actual task-relevant Cortex MCP calls and results: `Cortex tool error`, `validation_error`, `schema_unsupported`, traceback, or a missing success marker is failure. A repeated successful mutation without an explicitly ambiguous prior transport result is also failure; backend idempotency does not excuse an unexplained replay. For the stabilization example, require exactly one task-creation request and a non-replayed success before its sentinel. Capture the exit marker before stopping; cleanup stops the pipe and removes only `cortex-v12-smoke` plus its owner-only temporary capture. Never use `codex exec`, an alternate socket, or stable HOME/CODEX_HOME.

For every native worker spawned by live orchestration, the LLM verifier must inspect a bounded sanitized structured event stream as well as the coordinator pane because worker MCP calls/errors may be hidden. The helper may expose events but must not decide pass/fail. Acceptance requires a clean first worker-owned report-submission success, zero prior hidden validation/tool errors or mutation replays; a final report reference alone is insufficient.

The E2E acceptance case is multi-turn and runs in a separate test project. The LLM observes the pane, answers exactly one product clarification with the predefined safe answer, later approves the visibly rendered plan, and follows planner → implementation → independent verification → documentation-impact assessment → closure. It inspects every native worker event stream and fails on any hidden tool error or unexplained replay. The tmux transport never answers or approves autonomously.

After installation or source synchronization, run exactly one fresh interactive
Cortex task first. It must reach worker-verified acceptance and an advisory
`ready` closure without a coordinator boundary violation. Only after that
single-session pass may concurrent multi-session smoke begin.

Live tests are narrow and must exercise only the modified function, tool, or
contract in the active session. Do not use `codex exec`, an exec-mode wrapper,
or a detached substitute for the interactive tmux Codex session. Record the
session command, the launcher's printed `HOME`/`CODEX_HOME` target, refreshed
cache version, test scope, outcome, and unrun checks. If ordinary Codex cannot
start or a terminal permission prompt/denial prevents the targeted input or
result, classify the smoke as failed or unverified from the bounded capture;
never infer success. Always clean up the named session.

Exercise several explicit `$cortex:orchestrator` tasks:

1. Minimal bounded work with concise worker verification and closure.
2. Light work revised to full after new security evidence.
3. User-selected minimal for a risky task, preserving override plus warning.
4. `not_ready` closure followed by model-owned rework.
5. A project initiative linking several tasks/reports and closing with an
   unresolved dependency disclosed as residual risk.
6. A task with sufficient completed evidence whose advisory closure is absent
   or unavailable still receives a complete final answer; when the closure
   attempt returns `closure_confirmation.inspection_status=unconfirmed`, the
   coordinator discloses that bookkeeping limitation without changing or
   reopening the independent execution-outcome evidence.
7. A material verified change followed by a documentation-sync worker and a
   separate documentation-verifier worker before closure.
8. A verified no-impact task whose existing finalized reports either contain an
   explicit English documentation-impact section with a material/no-impact
   rationale or cause one bounded evidence-synthesis worker to submit it. The
   final initiative links that exact compact report ref and closure evidence
   cites it (with any durable report ID retained as non-callable evidence);
   the coordinator creates no documentation edit, never calls a worker-only `publish_*` operation,
   and never self-asserts `documentation_not_required`.
9. A task whose coordinator uses only the host-injected `AGENTS.md` context, both knowledge
   indexes, and one task-relevant linked page, compiles all six contract parts,
   and supplies them to a worker that does not independently redo routing.
10. A deliberately missing or stale index handled by a bounded discovery
    worker without direct coordinator source inspection or inferred harvest.
11. A required plan review that waits for an explicit decision bound to the
    finalized plan digest, then creates a revised plan and new review after a
    revision response.
12. A localized non-English user decision whose every child commentary,
    inter-worker message, final response, tool-authored durable string,
    worker-authored report narrative, task, decision, and task-view source
    remains English with task/decision `*_original` text preserved; a canonical
    report carrying source material preserves one unchanged `source_text`
    value without a language duplicate, and the coordinator publishes a
    localized summary and verified ready link.
13. A large worker result submitted atomically through `publish_result`, then
    read through the bounded `read_task` evidence view and resumed with
    `continue=true` without duplicate evidence.
14. A required-plan case where the user forbids coordinator project operations;
    project discovery/planning is worker-owned before the plan-review hold and
    the coordinator performs no project search.
15. A reportless/rework case where a user asks about project-local `.codex`;
    the coordinator delegates the existence/absence/unchanged check and never
    searches the project itself.
16. A live task with exactly two independent outcomes and one linked acceptance
    criterion per outcome; the effective contract and worker reconciliation
    must contain exactly two outcome items, while every delegation carries the
    six knowledge sections exactly once, in order, with non-empty
    delegation-specific content before native spawn.
17. The no-impact route creates/updates a final initiative with both the exact
    task relationship and every required finalized report link, closes the
    exact returned initiative, and verifies the closure and links through both
    task-scoped and initiative-scoped governance. A report-only initiative,
    premature ready claim, or edited/reconstructed ID fails the scenario.
18. Explicit activation uses the real `$cortex:orchestrator` token or host
    skill picker and normally receives complete host-supplied skill context.
    After compaction the `SessionStart(source=compact)` hook reinjects the exact
    packaged skills with `additionalContextLimit=0`; `PostCompact` is
    observation-only, and the host skill loader may repeat
    the same load whenever needed. It uses no `cat`, filesystem/shell read,
    approval, elevated execution, MCP resource, project copy, or `skill://`;
    decorative bracket text is not activation.
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
22. For light/full delivery, and whenever minimal work otherwise needs planning,
    the coordinator creates a planner delegation and never writes the solution
    plan. Light/full delivery is rejected until the planner's current finalized
    required-review `plan` has an explicit approval bound to its exact digest.
23. A material report, decision, failed/incomplete check, changed risk,
    contradiction, scope change, or documentation finding loads
    `adaptive-pipeline`; evidence can add, remove, reorder, retry, or
    parent-link rework unstarted stages while completed evidence remains
    immutable.
24. The coordinator's standard Codex To-Do projection contains only current
    pipeline stages and review state, is refreshed whenever either changes, and
    never becomes a worker-subtask checklist or report-body mirror. The concise
    worker handoff includes current stage/state, outcome, next owner/action,
    pipeline/review delta, changed or verified surface, exact report ref/digest,
    and residual risk or unrun checks so routine coordinator report-body reads
    are unnecessary.
25. Russian coordinator-to-user updates lead with result, impact, and next
    safe step and reveal technical detail progressively, while all worker,
    inter-worker, ledger, and worker-authored report content remains English;
    any canonical report `source_text` is carried unchanged as inert source
    material.

Confirm native Luna dispatch omits the model override while Terra/Sol carry
exact overrides and every selected effort is preserved. Confirm no hook trust,
native stop barrier, backend-enforced fixed wait/read sequence, or server
recovery route appears; the coordinator must still wait for or reconcile its
exact spawned worker before consuming that worker's finalized report.

Inspect the coordinator chronology. Its only non-user actions may be Cortex
ledger calls, native worker coordination, and the bounded orchestrator-owned
knowledge route. The host-injected `AGENTS.md` context governs the task; it then reads the two knowledge indexes,
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
- V12/1.14.9/schema-v1 identifiers;
- exact fourteen-tool names;
- explicit `project_root` only on `open_task`, compact `task_ref` on the
  task-anchored tools, exact task/result contract fields, arbitrary optional
  task `context`, textual delegation scope, exact model/effort, bounded
  inspections, evidence only through `read_task`, and task-scoped closure
  fields;
- publication schemas, plan review policies, private/internal chunk limits and
  replay, and task-ref-only user-decision/original-language semantics;
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
- absence of active lifecycle telemetry, hooks, waves, gates, receipt-gated
  lifecycle, capabilities, and server recovery claims;
- coordinator-only orchestration, worker-owned project action/analysis, and the
  conditional report-grounded documentation stage before closure;
- opaque byte-for-byte public `task_ref` and server-rendered evidence values,
  private durable IDs/digests/continuation, worker-only `publish_*` operations,
  task-scoped closure fields, and advisory ready claims based on ledger evidence;
- complete English-only child transcripts and a finalized worker-owned
  documentation-impact publication confirmed through `read_task` evidence;
- single-authority bounded knowledge routing, complete non-empty
  per-delegation six-part contracts, non-empty task/result arrays, and profiles
  that consume rather than reconstruct routing;
- canonical host-private database ownership, zero `project_root` projection
  writes, exact human-view layout/statuses, and verified ready-link publication
  with localized summaries. See
  [human-readable task views](../features/human-readable-task-views/index.md).

Record every exact command, outcome, environment, skipped scenario, and material
limitation. Never claim an unavailable host or live-model check passed.
The live-smoke script is transport-only and performs no readiness, trust,
rollout, sentinel, acceptance, approval, error, or retry parsing; the
coordinator/LLM decides from attached or bounded owner-only captured output.
Its pipe never feeds input. Prompt transport is one literal normalized insertion
followed by one separate explicit `Enter` key call; `enter` is only an explicit
single-key action after a visibly observed trust screen.
Use `./scripts/cortex-live-smoke start --workdir PATH` for a separate canonical
test-project cwd; candidate refresh still uses this repository's absolute
`scripts/cortex-dev`.
The launcher restores the selected workdir before starting ordinary Codex, so
the task project root follows `--workdir` while refresh remains repository-rooted.

<!-- GENERATED:END -->
