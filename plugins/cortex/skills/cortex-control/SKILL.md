---
name: cortex-control
description: Use this skill when coordinating a non-trivial task across Codex agents and durable gate, delegation, lock, or handoff state is useful. It uses the local cortex MCP server; apply Cortex model policy and bounded coordinator overrides at dispatch time.
---

# Cortex Control

The public Cortex API exposes exactly nine tools: three coordinator lifecycle
operations plus scoped worker question/report transport. Coordinators use `start_orchestration` and
`continue_orchestration` for normal work, `read_worker_report` to evaluate a
persisted report, and `manage_orchestration` only for recovery or rare
subsystems. Workers use `worker_question`, `get_report_template`,
`validate_report_draft`, and `record_report`; a worker whose
host filesystem read cannot open its exact briefing may call
`read_dispatch_briefing` with the complete identity/digest tuple from its
bootstrap. If a bounded response is incomplete, it may continue only with the
returned opaque cursor until `complete=true`. A successor worker may also use `read_worker_report` with its exact attempt/profile only
for predecessor refs explicitly supplied in its dispatch. Workers must not call lifecycle
operations. The private component API and retired public `orchestrate` facade
must never be called by a coordinator or worker. Cortex remains explicitly
opt-in through a non-help, non-`normal` `cortex:orchestrator` route.

## Root coordinator lock

An active Cortex root is coordination-only. It must not use project-reading,
search, shell, filesystem, patch, build, test, or execution tools on the target
project. It may call Cortex, invoke the exact returned worker dispatches, wait,
route questions, assess reports, and communicate with the user. Every project
operation must be delegated, including investigation after a plan and small or
obvious edits. The root must remain idle while a worker runs. Worker failure,
delay, unavailable dispatch, or incomplete evidence is a blocker or rework
signal, never permission for the root to perform the work directly.

## Normal flow

1. Call `start_orchestration` once with the exact absolute `project_root` and
   the user's exact, unexpanded text in `task.user_request`. Omit
   `task.objective`; its compatibility form is accepted only when it exactly
   matches `user_request`. Add requirements, acceptance criteria,
   scope, allowed paths, verification, budget, pause conditions, language, or
   complexity only when the user supplied them or they are established facts.
   Do not make an abstract request look decision-complete by inventing product
   intent, audience, design direction, behavior, or acceptance. Complexity
   defaults to C2 and accepts aliases. Before the one start call, verify that
   ordinary tasks have non-empty `task.acceptance_criteria` and
   `task.verification`, derived only from the exact request or verified
   authority. If either list cannot be grounded without inventing material
   intent, ask the user before calling Cortex. Exact harvest routes are the
   sole exception because Cortex supplies their exhaustive census contract.
2. The coordinator owns the pipeline decision. It may consciously accept the
   standard quality-preserving pipeline or supply `waves`; Cortex stores,
   returns, and validates that plan and enforces documentation and close. An override uses only
   `waves: [{workers: [{phase, ...}]}]`. `phase` is required; optional fields
   are `profile`, `objective`, `paths`, `acceptance`, `verification`, `model`,
   `user_requested_model`, `effort`, `depends_on`, `context_files`, `visible`,
   and `isolated_checkout`. `depends_on`
   names exact completed or earlier prerequisite phases; omit it to receive all
   verified predecessor reports. `context_files` carries exact project/feature
   knowledge pages selected by the planner for that worker.
3. Invoke every returned dispatch with its exact `call` and `arguments` in one
   model turn when the host supports parallel tool calls. The children execute
   concurrently; correlate each `SubagentStart` by the exact returned
   `task_name`/`dispatch_ref` (and the host child id), never by a guessed
   ordinal or a display label. If the host cannot batch calls, issue them in
   the returned order as a transport fallback, but preserve the same exact
   correlation contract.
   Before invoking it, check the sibling `phase`, `profile`, `capability`,
   `sandbox`, `selection_reason`, `dispatch_ref`, `briefing_path`, and
   `briefing_digest` against the latest worker evidence and
   the canonical roster in `cortex:orchestrator`. Arguments contain only
   native host parameters. Their message is intentionally only a compact
   bootstrap: the complete prompt is the one immutable briefing named by the
   returned path and digest. The coordinator must not read, inline, expand, or
   reconstruct that file or expose the surrounding ledger. Never add ledger
   IDs or copy an expected model into a missing native `model` field. Hidden `spawn_agent` dispatches must retain
   the returned `fork_turns: "none"`: the generated Cortex briefing is the
   complete worker context, and inheriting the coordinator transcript can leak
   localized user-language messages into the English-only worker channel.
   A dispatch is successful only when the native call returns its child id.
   Never announce that a worker was sent or wait before the returned child id
   is durably bound. A host-level wait-any representation may omit an explicit
   target list only while Cortex has a bound running child; otherwise it is
   denied as an unspawned dispatch. If a native call is unavailable or fails,
   stop with that blocker; otherwise wait only for bound children.
4. Workers do not call lifecycle operations. A worker first reads only its
   exact briefing path, confirms the file is
   read-only and its SHA-256 equals `briefing_digest`, and stops on any
   mismatch. That path is the sole direct-read exception below
   `.codex/cortex`: never list or inspect the directory, mutable state,
   baselines, delegation packages, another briefing, or report files. If the
   host filesystem read says this exact path is missing or unreadable, the
   worker may call `read_dispatch_briefing` with the exact project root,
   task id, attempt id, profile, dispatch ref, and digest from the bootstrap.
   An incomplete bounded response may continue only with its returned cursor.
   That scoped fallback returns only the same validated briefing and grants no
   directory or ledger access. If it also fails, stop with its exact diagnostic.
   After reviewing it, the worker includes the exact bootstrap-supplied `Dispatch
   briefing reviewed: <sha256>` item in `report.evidence`; `record_report`
   verifies the file again and rejects a missing marker or changed artifact.
   Read-only workers must select non-writing verification modes before running
   commands: use `PYTHONDONTWRITEBYTECODE=1` for Python, disable pytest and
   equivalent test/build caches, and skip any check that requires cleanup.
   They must never create an artifact and then use `rm`, `git clean`, or a
   cleanup script. The result validator compares both ordinary files and
   generated/gitignored artifact sentinels against the attempt baseline.
   Predecessor reports remain accessible only through scoped
   `read_worker_report`. While the wave is active, the coordinator is in
   `waiting_workers` with `output_policy="silent"`: repeated wait timeouts
   produce no heartbeat or status commentary. Visible output is limited to a
   worker question, worker completion/failure, or a blocking error.

   Any worker may call `worker_question` when repository evidence cannot
   resolve a material user decision. Before pausing, it collects every
   currently known material decision: use `action="ask_batch"` when there is
   more than one, and `action="ask"` only when exactly one is known. It returns
   a compact `question_ref`; the worker sends only that ref and a concise
   question summary through the native parent
   channel, publishes no report, and finishes its native turn into an
   idle/resumable state rather than busy-waiting. The coordinator calls
   `manage_orchestration(intent="question", payload={"question_ref": "<exact
   ref>"})` exactly once; Cortex owns task/principal/thread resolution and opens
   the host-native question UI. A batch is rendered sequentially, one native
   question at a time, and every accepted answer is checkpointed before the
   next appears. Never ask the question in commentary/final prose or guess an
   internal identity. After the answer is durably recorded,
   resume the exact same native worker through `followup_task`; that worker
   calls `worker_question(action="poll")` with the same attempt and ref before
   continuing. Never replace the worker or advance the wave for a question.
   After work completes, the worker gets its exact gate-specific skeleton from
   `get_report_template`, replaces every placeholder, and repeats
   `validate_report_draft` until `draft_valid=true`. Draft validation persists
   nothing and consumes no worker attempt; only failed worker attempts count
   toward the three-attempt recovery budget. It then publishes the exact
   unchanged strict `cortex/report/v1` once through `record_report` with the
   returned `validation_digest` and returns only
   `REPORT_RECORDED report_ref=<value>` plus at most a
   two-sentence summary. They must never paste the report JSON into the parent
   channel. When predecessor handoffs are supplied, they review all of them and
   include the generated `Predecessor review:` acknowledgement in report
   evidence; the report tool enforces complete acknowledgement.
   A successor worker reads each supplied predecessor ref before repository
   work through `read_worker_report`, passing the exact project root, task ref,
   attempt id, profile, and supplied report ref from its generated briefing.
   Cortex rejects attempts to read an ungranted report. This scoped read does
   not authorize coordinator lifecycle calls or user-facing report links.
   A final report always has `questions: []`: material decisions must complete
   the durable question lifecycle first, and non-blocking evidence gaps belong
   in `uncertainty`.
   Every gate report must carry a separate top-level `gate_result` with
   `decision`, `failure_class`, `findings`, `verification`, and `workspace`.
   This is the canonical result envelope for implementation, QA, review, close,
   and every other gate. The older top-level `closure` sibling is retained only
   as a temporary compatibility alias for review/close and must not be placed
   inside the strict seven-field report; when both are supplied they must agree.
   For C2/C3 close attempts, each executed test or verification result also
   requires a non-empty concrete summary of observed output or behavior. Concise
   summaries are valid; no arbitrary word count applies, and completion
   assertions without observed output or behavior are rejected.
   Every invalid draft returns field paths and fixes. Correct every named field
   and validate the complete payload again on the same task and attempt. A
   changed payload invalidates the prior digest; `record_report` atomically
   revalidates before persistence. Stop only for a non-retryable error or when
   exact report identity is unavailable.
   `followup_task` resumes the same addressable native worker for an answered
   durable question or an explicit active steer. Active steer is recorded as a
   new task revision and delivered to the existing `host_agent_id`; it does
   not create an attempt or a replacement worker. If native worker completion contains a
   `record_report` error or anything other than `REPORT_RECORDED` or
   `QUESTION_RECORDED`, do not send a corrective follow-up: `SubagentStop` has
   already classified that attempt. Call
   `manage_orchestration(intent="inspect")` once, then consume a recovered
   report, route the durable question, or submit the exact failed result that
   inspect returns. Only a newly returned top-level dispatch authorizes rework
   after a worker is no longer resumable.
   Cortex permits at most three automatic failed attempts for one active phase;
   it then blocks with a durable handoff. Resume only after repairing the
   recorded cause, which resets that phase's bounded recovery counter.
5. After all workers finish, read every ref with `read_worker_report`. Each
   result includes Cortex's derived absolute `report_markdown_path` for the
   persisted `reports/markdown/<report-ref>.md` artifact. After reading each
   completed report, immediately publish the returned `report_markdown_link`
   verbatim as a compact clickable Markdown link in the main chat, before any
   other lifecycle call or additional report read. This is mandatory coordinator
   output, not optional metadata. The link supplements the concise summary and
   report review. Never guess, substitute, or use the path to browse unrelated
   files. Then evaluate
   the reports against the pipeline, then call `continue_orchestration` exactly
   once with `project_root`, the opaque `task_ref` and relative `step` from the
   prior response, and all `report_ref` results. A single-worker result may omit
   its slot; parallel results repeat only the returned integer `worker` slot.
   Omit status for success; non-success requires normalized `status`, `reason`, and
   the exact `dispatch_ref` from that stopped worker's returned dispatch (or
   from `context_handoff.stopped_workers`). It omits `report_ref`. This binds a
   failure to one attempt, so a duplicate stale failure can never be applied to
   its replacement. Until all workers finish, remain idle and perform
   no project operation. A `SubagentStop` after `record_report` is recovered
   from the persisted report ref; a stop on an open durable question remains
   resumable; any other stop is durably failed and must be submitted as a
   non-success result for canonical rework. Never wait on or respawn a stopped
   child directly.
6. Repeat one continue per completed wave. Finish only when `outcome` is
   `completed`; Cortex has then reconciled reports, evidence, documentation,
   close verification, the manifest, and handoff.

### Recovery after context reset or compaction

If the host compacts or clears the conversation, or resumes the task with a new context
window, or the coordinator no longer has the exact Cortex protocol in active
context, preserve the opaque `task_ref` and call
`manage_orchestration(intent="inspect")` exactly once for that task. Use the
returned `context_handoff`, current pipeline, report refs, and relative step
as the authoritative recovery snapshot. Invoke only top-level inspect
`dispatches` that correspond to `context_handoff.pending_dispatches`; the
handoff itself is descriptive, not spawn authority. Never respawn entries in
`active_workers`; wait only on their exact persisted `host_agent_id` values.
The documented `SubagentStart` hook binds each native child id/model to the
exact returned dispatch identity before project work (`agent_type` is
`default` for dynamic workers), so inspect can distinguish those states.
If a running attempt has no child id, fail closed instead of spawning or
waiting without a target. Do not call `start_orchestration`
again, replay completed dispatches, or reconstruct state from a raw
transcript. After rehydration, continue the existing task and publish every
exact `report_markdown_link` before the next lifecycle or report-read call.

### Required post-plan approval

`task.plan_approval` accepts `auto` or `required`; Cortex defaults to
`required` for C2/C3 and `auto` for C1. The C1 auto policy does not require
user confirmation. A required plan must be its own wave. Once that plan
completes, the lifecycle result is `awaiting_plan_approval` with no successor
dispatch and a bounded `plan_review` containing `report_ref`, `summary`,
`findings`, `uncertainty`, and `remaining_phases`. The
coordinator reads the report, summarizes the plan in the main chat, then calls
`manage_orchestration(intent="plan_approval", payload={"decision":"prompt"})`.
Cortex opens the native `Approve` / `Cancel` UI. On `Approve`, announce in the
user's language that the plan was approved and dispatch the returned wave. On
`Cancel`, stop silently, keep the plan pending, and wait for the user's next
message. For requested changes after that message, use
`manage_orchestration(intent="plan_approval", payload={"decision":"revise", "feedback":"..."})`;
feedback is required and the Planner runs again before approval. This gate is
separate from `worker_question`: material questions are resolved through that
lifecycle during planning rather than through a duplicate approval question.

The Planner may attach a separate `planning` object to its public
`record_report`. It contains exactly `overview` and `work_packages`; the
strict seven-field `cortex/report/v1` contract remains unchanged. Each package
has `id`, `title`, `objective`, optional `allowed_paths`/`depends_on`, and
non-empty microtasks; `profile` is forbidden at package level. Each microtask
requires `id`, `title`, `objective`, non-empty `acceptance_criteria`, and
non-empty `verification`, with optional `profile`, `allowed_paths`, and
`depends_on`.
Cortex requires microtask IDs to be globally unique across the plan, allows
`depends_on` to reference microtasks in another work package, rejects unknown
references, and validates the combined microtask dependency graph as acyclic.
It enforces 32 packages, 32 microtasks per package, and 128 total microtasks.
The Planner remains read-only; Cortex materializes immutable, revision-scoped
`.codex/cortex/tasks/<task>/planning/revisions/plan-<report-ref>/overview.md`
and `packages/<id>.json` artifacts. The SQLite task document
`planning_current` is the sole current-plan pointer; there are no
`planning/manifest.json` or `planning/overview.md` latest aliases.
`plan_review` exposes compact
`planning_artifacts` for approval. Treat this as a durable catalog for
ownership/dependency-aware scheduling within the canonical phase/wave safety
model, not as an unconstrained auto-executor.

### Active steer and correcting a completed task

While a task is active or blocked, a material user correction uses
`manage_orchestration(intent="steer")` (aliases `amend` and
`revise_active_task`) with `payload.user_message`. English worker delivery is
required; for another user language the coordinator supplies canonical
English `message_en`. Cortex appends a task revision, retains the original and
canonical messages, computes a bounded impact summary, and returns
`followup_task` calls only for addressable active native workers. If a worker
session has no `host_agent_id`, the steer remains durable and the coordinator
must inspect/continue the revised pipeline rather than guessing a resume target.
This is distinct from `follow_up`, which creates a linked task only for a
completed source.

### Correcting a completed task

Completed source tasks are immutable: do not reopen or mutate them. For an
exact corrective request, call
`manage_orchestration(intent="follow_up")` with the completed source
`task_ref` and `payload.user_request` preserving the user's wording. Cortex
creates a linked corrective task with its own `task_ref`, pipeline, and
dispatches. Its workers receive source-derived handoff and report Markdown
paths as historical context only; they must revalidate consequential claims
against current source and tests. `payload.report_refs` is optional and
bounded to at most 32 source refs; when omitted, Cortex selects a bounded
recent set. If the source task is active, use evidence-based `rework` instead;
`follow_up` rejects active sources.

`read_worker_report` returns the derived absolute `report_markdown_path` for
the persisted `reports/markdown/<report-ref>.md` artifact. After reading each
completed report, publish a compact clickable Markdown link in the main chat
using that exact returned path, in addition to the concise summary and report
review. Never guess, substitute, or use the path to browse unrelated files.
Inspect `available_reports` and required-plan `plan_review` expose the same
derived path for recovery and approval review.

Normal requests never carry caller-generated submission, task, wave, attempt,
principal, thread, host-tool, host-model, or host-effort fields. Internal IDs
and receipts remain durable below `.codex/cortex`.

## Idempotency and relative references

Start resumes an identical unfinished request automatically. Continue replays
a byte-identical retry for its internal active wave. The relative `step`
distinguishes identical report content used on successive waves without
exposing durable identity. Parallel worker slots are complete, unique, and
validated atomically before task state changes.

Every successful start response says whether it is a replay. Once start
returns dispatches, it is complete: invoke those dispatches and never call
`start_orchestration` again for that `task_ref`, including while translating
or preparing native arguments. A replay returns no dispatches and cannot
authorize a second worker wave. If the first response was lost before native
dispatch, recover still-awaiting requests once through management inspect.

Preserve the `task_ref` returned by start and pass it on later lifecycle and
report-read calls. Different task contracts can run concurrently below one
project root; the project registry is lock-serialized and task records remain
isolated. An exact duplicate active start is an idempotent replay. If a caller
omits the ref while several tasks are active, Cortex returns `needs_selection`
with objectives and opaque refs instead of guessing.

## Adaptation and recovery

The returned `pipeline.waves` snapshot is the current coordinator-owned plan.
Follow it by default. Pass compact `future_waves` only when the coordinator
decides that verified evidence materially changes work that has not started;
include a concise `reason`. Planner and explorer ownership recommendations are
advisory routing evidence, not an automatic rewrite command. Prefer the
narrowest supported profile and replace a stale route only after that explicit
decision. `general` is a conservative fallback, not the preferred universal
writer. Repeating a completed phase requires `rework: true`. Use
`manage_orchestration` for `inspect`, `resume`, `deactivate`, `lane`,
`resource`, or `question`; these intents do not belong in normal wave calls.
Follow recoverable diagnostics and never fall back to private tools.

The question intent accepts only the worker's exact `question_ref` on the
normal path, resolves all durable identity internally, and requests main-chat
MCP UI elicitation through `elicitation/create`. The coordinator may pass
`localized_question`, `localized_header`, `localized_options`, and
`localized_custom_label` as transient user-language labels; the stored
question remains canonical English. Answers preserve the user's original
value and language and require `answer_en` for localized free text before the
worker receives the canonical English answer. Workers may use
`worker_question(action="ask_batch")` with 1–32 stable questions and poll the
same `batch_ref` with `action="poll_batch"`; the host renders one question per
native step and durably checkpoints each accepted answer before showing the
next. Cancellation preserves completed steps and resumes at the next unanswered
item. A task revision supersedes an unresolved batch rather than resuming stale
user intent. Every
worker classifies unknowns as repository-resolvable, low-impact reversible, or
material user decisions. Only the last class pauses through `worker_question`;
existing code is current-state evidence, not evidence of desired product
intent. Cortex rejects `record_report` and `continue_orchestration` while a
blocking question remains open, rejects any non-empty final report question
list, and requires an answered blocking question before decision-bearing
phases can complete when deterministic intent preflight marks a short product
surface request as underspecified. Lack of advertised host elicitation support
is a fail-closed host limitation, not permission to invent an answer or ask the
question as an ordinary message.

Project maintenance uses `manage_orchestration(intent="prune",
payload={"confirmation":"PRUNE","older_than_days":7})` only after the user
explicitly selects the `prune` route. It removes task-scoped Cortex ledger
state only for completed tasks whose last update is at least seven days old.
Active and blocked tasks are preserved regardless of age, as is every
classification receipt referenced by a retained task. Recent completed tasks,
lanes, plugin files, project source, and documentation are also preserved. It
is project-scoped, requires exact `PRUNE`, must omit `task_ref`, and is safe to
run weekly; do not use an unbounded clear operation. When no retention period
is supplied, the route presents `keep_1d`, `keep_7d`, `keep_30d`, and
`full_reset`. The first three are bounded retention selections. `full_reset`
is separately destructive and requires the exact confirmation `RESET CORTEX`;
it fails closed while active workers exist and removes only project-scoped
`.codex/cortex` state, never project source or documentation.

## Dispatch and evidence policy

Use the adaptive model policy defined in `profiles.json`. `explorer` always
selects Luna; the coordinator chooses its effort, with the risk-based default
used when omitted, and Terra is permitted only as the hidden host-unavailable
fallback. Security context, the security gate, and `security_auditor` always
select Sol, with minimum effort `medium` for C1, `high` for C2, and `xhigh` for
C3. Ordinary profiles are divided into efficient, adaptive, and deep classes.
Efficient work uses Luna. Deep profiles use Terra. Adaptive work stays on Luna
for low/moderate-risk work when no `terra_task_kinds` trigger is present.
C2/C3 planning, uncertain diagnosis, long-context or integration-conflict
work, and high/critical failure cost use Terra. Efficient Luna uses C1/C2/C3
`high`/`high`/`xhigh`; bounded adaptive Luna uses `high`/`xhigh`/`max`; Terra
uses `high`/`high`/`xhigh`. Risk floors remain low/moderate `medium`, high
`high`, critical `xhigh`. The complete vocabulary is `low`, `medium`, `high`,
`xhigh`, and `max`; never request another value. Automatic `max` is limited to
bounded C3 Luna work. A coordinator may explicitly override an
ordinary route between Luna and Terra, but cannot lower its effort floor.
Non-security Sol is accepted only when the user explicitly selected it.
Set compact `user_requested_model: sol`; omit `model` or also set it to `sol`.
Cortex records matching `user_requested_model` and `requested_model`.
Coordinator preference, an earlier Terra failure, and auditable-extreme labels
do not authorize Sol;
the retired `sol_escalation` and model/effort remapping contracts must not be
used.

Configured-default Luna routes carry explicit effort but omit native `model`;
explicit Luna/Terra/Sol selections retain it. If Luna is unavailable to the
host, Cortex may use a hidden Terra fallback while preserving the selected
effort. Expected routes are metadata, not proof of the effective host model.
Only host-observed runtime metadata may attest actual models. The original
user language is held by the main coordinator only. Workers emit English in
every message, tool argument, report, durable question, handoff, and native
final response. Durable worker questions remain English; the coordinator may
pass `localized_question`, `localized_header`, `localized_options`, and
`localized_custom_label` as user-language UI projections without altering the
durable record. A corrective `follow_up` inherits the completed source task's
user language, but workers retain the same English-only protocol.

Generated worker briefings carry a bounded Codebase Memory contract. When the
`mcp__codebase_memory__*` tools are actually available, each briefing supplies
the exact project key precomputed from canonical `project_root` using Codebase
Memory's `cbm_project_name_from_path` rule: preserve ASCII
`[A-Za-z0-9._-]`, replace other ASCII with `-`, hex-encode non-ASCII UTF-8
bytes, collapse repeated dashes/dots, trim invalid edges, use `root` when
empty, and cap at 200 bytes with an eight-hex FNV-1a suffix. Workers use that
key directly and must not call `list_projects` before the first indexed query.
Only direct not-found, ambiguity, or apparent key drift/collision permits one
`list_projects` fallback, whose entry must match the exact canonical root;
basename matching is forbidden. Workers prefer graph, architecture, trace, and
impact tools for non-trivial discovery and confirm consequential facts in
current source or tests. Designated read-only discovery
profiles may refresh one missing or stale index; other profiles fall back to
repository-native tools. One failed MCP attempt is enough: record the
limitation and do not loop. The coordinator never calls repository-intelligence
tools itself because the root lock still applies.

When `docs/project/index.md` or `docs/features/index.md` exists, Cortex adds it
to every worker briefing without asking the coordination-only root to inspect
the project. The planning worker reads both indexes first, selects all linked
pages relevant to the task boundary, and records the recommended paths in its
report. The coordinator attaches those paths through later-wave
`context_files`; downstream workers also re-check the indexes for missed
cross-feature dependencies. Documentation is a navigation layer and prior,
not authority: workers confirm consequential claims in current source, tests,
schemas, or executable configuration. Every worker report must include one
`Knowledge reviewed:` evidence entry naming both available indexes and every
additional knowledge page actually used. The report tool rejects an omitted
index acknowledgement.

Canonical phases are `scope`, `plan`, `discover`, `architecture`,
`database_architecture`, `implementation`, `qa`, `security`, `performance`,
`accessibility`, `ux`, `review`, `documentation`, and `close`. One phase may
appear in only one wave; multiple owners for a phase share that wave. Generic
`verification` maps to `qa`, while `build_verification` and
`final_verification` map to `close`.

Every report remains quota-, redaction-, path-, and receipt-checked. Cortex
fails closed on root or symlink violations, stale steps, invalid slots,
changed retries, missing sections, invalid rework, failed close verification,
manifest mismatch, incomplete predecessor or knowledge-index acknowledgement,
or handoff context that exceeds its safe count/size budget. It never silently drops an older
report; narrow the dependency set with `depends_on`.

## Durable artifacts

Every call supplies its exact absolute `project_root`. Runtime state stays in
`${project_root}/.codex/cortex` using the canonical `cortex/v8` ledger.
`CORTEX_ROOT`, `/tmp` fallback, and symlink traversal remain forbidden.
Initial and per-attempt project manifests are immutable, content-addressed
SQLite records referenced from state by compact
`manifest-<sha256>` refs. Identical state deduplicates, but every dispatch
captures again to detect external changes. Terminal close persists completed
state before removing database manifest records;
final receipts retain digest/change proof. `allow_rework` reopening captures a
fresh active baseline before replacement dispatches.

## Ownership, safety, and verification

Parallelize read-only exploration, review, testing, and analysis when their
dependencies permit it. Assign exactly one writer to an overlapping code or documentation area.
Independent write streams require separate worktrees and
must still reconcile predecessor evidence before integration.

Never place secrets, credentials, private tokens, personal data, raw private
reports, or sensitive operational detail in task inputs, worker prompts,
questions, reports, handoffs, logs, or user-visible summaries. Redaction is a
defense in depth measure, not permission to transmit sensitive input.

Before completion, run the smallest non-destructive verification set that
proves the affected acceptance and verification contract, then broaden checks
in proportion to risk. Read-only workers select non-writing modes before they
run: disable bytecode and test/build caches and skip checks that require
cleanup. Never create an artifact and then delete it to simulate read-only
verification. Report every unrun required check, environmental limitation, and
remaining uncertainty plainly. Current source, tests, schemas, and executable
configuration outrank generated documentation.

## Private tool-error diagnostics

Cortex appends raised MCP exceptions and legacy error-shaped tool results as
JSONL to `~/.codex/logs/cortex-tool-errors.jsonl`, where `~` is the home of the
user running the MCP process. This is private per-user diagnostic data, not the
project ledger. The writer keeps the file at or below 10 MiB by dropping the
oldest complete records and retaining the newest complete records before each
append. Expected public validation and recovery responses with `ok: false` are
not exceptions and are not written to this log.

Records contain bounded correlation metadata such as timestamp, method, tool,
error type, `chat_session_id`/`thread_id`, request id, and supplied durable ids.
Common credential shapes are redacted, nested values are bounded, the parent
directory is mode `0700`, the file is mode `0600`, and symlink paths are
rejected. These controls do not guarantee arbitrary input is non-sensitive:
never put secrets in tool inputs, relax permissions, commit the log, or copy
raw records into a prompt, chat, issue, ticket, or external system.

For local read-only diagnosis, inspect only a small tail and project an
allowlist such as `timestamp`, `event`, `method`, `tool`, `error_type`,
`error`, `chat_session_id`, `thread_id`, `request_id`, and `ids`. If `jq` is
unavailable, parse UTF-8 JSONL locally with the same allowlist. A request
rejected by the host before it reaches the MCP server cannot appear here; use
the host or session diagnostics for that boundary.
