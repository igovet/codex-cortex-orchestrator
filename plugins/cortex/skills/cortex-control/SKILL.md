---
name: cortex-control
description: Internal Cortex runtime protocol. Load only after cortex:orchestrator has been explicitly activated. Never select directly for an ordinary task.
---

# Cortex Control

The public registry exposes nine MCP operations. The ordinary Desktop launch can activate
`$cortex:orchestrator` and call its lifecycle operations. The strict worker projection is `worker_question`, `record_attempt_event`, `complete_attempt`,
`read_dispatch_briefing`, and scoped `read_worker_result`. The explicit coordinator projection is
`start_orchestration`, `continue_orchestration`,
`manage_orchestration`, `manage_governance`, and scoped
`read_worker_result`. Coordinators use
`start_orchestration` and `continue_orchestration` for normal work,
`read_worker_result` to evaluate a persisted AttemptResult, and
`manage_orchestration` only for recovery or rare subsystems. A worker whose
host filesystem read cannot open its exact briefing may call
`read_dispatch_briefing` with the complete identity/digest tuple from its
bootstrap. If a bounded response is incomplete, it may continue only with the
returned opaque cursor until `complete=true`. Any worker-tool caller/input/schema
diagnostic or `retryable=true` result is corrected and retried on the same
attempt without consuming the recovery budget; it never ends the worker. A
blocked/failed result is terminal evidence for server-owned corrective
recovery, not a Cortex stop. Only a genuinely unavailable exact identity after
recovery is routed to a server-owned diagnostic worker. A successor worker may also use `read_worker_result` with its exact attempt/profile only
for predecessor refs explicitly supplied in its dispatch. Workers must not call lifecycle
operations. The private component API and retired public `orchestrate` facade
must never be called by a coordinator or worker. Cortex remains explicitly
opt-in through a non-help, non-`normal` `cortex:orchestrator` route.

The stdio MCP process has one immutable launch-time audience. An unspecified
or unknown transport audience uses the public nine-operation projection, so an
ordinary Desktop launch can activate `$cortex:orchestrator`. Explicit `worker`
and `coordinator` audiences remain
strict five-tool projections; JSON-RPC initialization and tool arguments cannot
select or elevate the audience. Coordinator capability recovery is accepted on
the explicit `coordinator` projection, and requires the exact
active task, principal, thread, and non-durable recovery proof returned with
the original successful authorization response. A lost recovery response is
retried with that proof; Cortex redelivers one HMAC-derived replacement pair.
Call `acknowledge_coordinator_recovery` with the prior proof and both returned
replacement values before the old pair is retired. Only opaque delivery
metadata and SHA-256 verifiers are durable. A lost initial start response
remains fail-closed without host attestation, and workers never receive either
secret. The worker prompt/profile remains worker-only; hosts that require
transport-enforced separation must use a strict `worker` or `coordinator`
projection at process launch.

## Coordinator state machine

| Current state | Event | Only allowed action | User output |
| --- | --- | --- | --- |
| inactive | explicit orchestrator activation | call `start_orchestration` once | concise activation summary |
| waiting_workers | wait timeout | wait again | silent |
| waiting_workers | worker question | render the durable chat interaction and end the turn | one detailed final chat message with LLM recommendation |
| active or recovery-pending | user changes the current task | `manage_orchestration(intent="steer")` on the same task | concise update |
| awaiting_plan_approval | approve | invoke only the returned next wave | approval update |
| awaiting_plan_approval | cancel | make no lifecycle call | silent |
| awaiting_plan_approval | non-empty custom response | invoke only the returned replacement Planner wave | revision update |
| completed | user asks to correct the result | `manage_orchestration(intent="follow_up")` | linked-task summary |
| context uncertain | compaction or reset | `manage_orchestration(intent="inspect")` once | recovered-state summary only when action is needed |

### Natural-facing coordinator contract

The state machine above is internal protocol; never expose its state names,
tool calls, IDs, paths, cursors, worker details, or validation mechanics to the
user in the default `natural` profile. Translate each meaningful event into
3–5 short steps: result, importance, next action, and (only when needed) one
user decision. A user-facing message contains at most one actionable question.
While the state is `waiting_workers`, the coordinator is completely silent:
wait timeouts and unchanged worker state produce no heartbeat, progress note,
or repeated explanation.
A coordinator must not return a final answer while a durably bound worker is
running, but no hook may block or silence a coordinator turn. The Stop hook is
telemetry-only. If a coordinator is idle, call
`manage_orchestration(intent="inspect")` once and consume the returned active
child, result, question, or server-owned recovery receipt. A worker failure or
malformed lifecycle payload is always routed to same-attempt correction or an
automatic corrective dispatch; it must never stop Cortex itself.

### Technical recovery is never a Cortex stop

`blocked`, `needs_input`, `validation_failed`, stale receipts, invalidated
attempts, replay-registry drift, missing result projections, failed dispatches,
and lifecycle inconsistencies are internal recovery signals. They are never a
reason to end the coordinator turn, return a system blocker, or ask the user
what Cortex should do. The coordinator must let the server repair durable
state, preserve all canonical results, and emit the next corrective dispatch
or same-attempt retry. Only a durable worker question or explicit plan-approval
decision may stop the ordinary chat.

For one lifecycle event use one operation path: read the canonical result,
call `continue_orchestration` once with its server-derived continuation, and
follow the returned dispatch, wait, or question. Do not chain
`manage_orchestration` calls such as `inspect -> recover_inspect -> resume` to
compensate for a technical error. `manage_orchestration` is an explicit
maintenance/recovery surface, not a polling loop; at most one server-owned
recovery intent may be used when the continuation receipt itself was lost,
after which Cortex must repair and continue internally. Never return prose
such as “internal state is contradictory”, “Cortex is blocked”, or “retry
management”; record that fact in the JSONC audit event and route the task
forward automatically.

For plan approval, the planner supplies one canonical recommendation. Any
material finding or uncertainty must already have a concrete
`recommendation_actions[]` entry with `issue`, `action`, `plan_refs`, and
`verification`; the user must not be asked to invent the correction. Render
exactly four choices: approve with recommendations, approve without
recommendations, revise, or cancel. The LLM may explain the planner's one
recommendation, but must never replace it with a contradictory recommendation.
Never treat silence as approval.

## Root coordinator lock

An active Cortex root is coordination-only. It must not use project-reading,
search, shell, filesystem, patch, build, test, or execution tools on the target
project, Cortex plugin source/cache, `.codex` state, or runtime internals. For
**every public Cortex tool**, its bundled skill instructions, public MCP schema,
and exact returned response are the only protocol authority. Never inspect
plugin code or cache to infer fields, validation, recovery, or behavior. It may
call Cortex, invoke the exact returned worker dispatches, wait, route questions,
assess results, and communicate with the user. Every project operation must be
delegated, including investigation after a plan and small or obvious edits. The
root must remain idle while a worker runs. Worker failure, delay, unavailable
dispatch, or incomplete evidence is a same-task recovery signal, never a
Cortex system stop and never permission for the root to perform project work
directly or inspect sources. If a condition requires a user choice, surface
one concrete question and keep the task resumable.

## Schema-first tool calls

Before every Cortex tool call, read the exact schema advertised for that tool by
the active MCP `tools/list` surface and construct one complete request from that
schema. Do not infer field names, enum values, nested paths, or phase aliases
from a prior response or from prose. A validation response is a form receipt:
apply every returned diagnostic to its named path, preserve all fields that
already passed validation, and retry the same operation only after the whole
request conforms to the advertised schema. Never send a guessed field and never
put a coordinator lock or internal runtime instruction in place of the concrete
schema repair.

## Turn-local read discipline

Maintain a turn-local evidence index of every fully read skill, file, and
bounded source range. Read each exact path only once per coordinator turn;
reuse that evidence for every later decision, lifecycle call, and user update.
Never repeatedly read the orchestrator skill, Cortex Control skill, or the
same project file to compensate for uncertainty. A second read is allowed only
when the first response was explicitly truncated or paginated, the file changed
after it was read, or a different not-yet-read range is required. Search before
opening a large file and read only the needed range. This discipline does not
authorize the coordination-only root to read project, plugin, cache, or ledger
paths forbidden above.

## Normal flow

1. Call `start_orchestration` once with the exact absolute `project_root` and
   the user's exact, unexpanded text in `task.user_request`. Add requirements, acceptance criteria,
   scope, allowed paths, verification, budget, pause conditions, language, or
   complexity only when the user supplied them or they are established facts.
   `task.verification` is the non-empty array of concrete authoritative checks;
   it is not a mode selector. In particular, never add a `verification_mode`
   task field or any other field absent from the public `start_orchestration`
   schema: Cortex rejects unknown task fields before creating a task.
   Do not make an abstract request look decision-complete by inventing product
   intent, audience, design direction, behavior, or acceptance. Complexity
   defaults to C2 and accepts aliases. Before the one start call, verify that
   ordinary tasks have non-empty `task.acceptance_criteria` and
   `task.verification`, derived only from the exact request or verified
   authority. If either list cannot be grounded without inventing material
   intent, ask the user before calling Cortex. Exact harvest routes are the
   sole exception because Cortex supplies their exhaustive census contract.
   A `start_orchestration` result with `ok=false`, `task_created=false`, or no
   `task_ref` did not create a recoverable task: do not call `manage_orchestration`,
   inspect, list, infer, or select another task. Record the internal
   reconciliation diagnostic and retry the originating lifecycle request once;
   this is never a user-facing Cortex blocker.
2. The coordinator owns the pipeline decision. It may consciously accept the
   standard quality-preserving pipeline or supply `waves`; Cortex stores,
   returns that choice, validates executable shape, and records governance
   recommendations without enforcing documentation or close conventions. An override uses only
   `waves: [{workers: [{phase, ...}]}]`. `phase` is required; optional fields
   are `profile`, `objective`, `strategy`, `paths`, `acceptance`, `verification`, `model`,
   `user_requested_model`, `effort`, `depends_on`, `context_files`, `visible`,
   and `isolated_checkout`. `depends_on`
   names exact completed or earlier prerequisite phases; omit it to receive all
   verified predecessor results. `context_files` carries exact project/feature
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
   identifiers to the bootstrap. Prompt volume targets are advisory only: do
   not truncate, omit, reject, or summarize away material task, plan, result,
   event, question, answer, or artifact data to satisfy a byte/character/file
   target. Complete content may be sent intact and is stored intact by the
   backend.
   IDs or copy an expected model into a missing native `model` field. Hidden `spawn_agent` dispatches must retain
   the returned `fork_turns: "none"`: the generated Cortex briefing is the
   complete worker context, and inheriting the coordinator transcript can leak
   localized user-language messages into the English-only worker channel.
   A dispatch is successful only when the native call returns its child id.
   Never announce that a worker was sent or wait before the returned child id
   is durably bound. A host-level wait-any representation may omit an explicit
   target list only while Cortex has a bound running child; otherwise it is
   denied as an unspawned dispatch. If a native call is unavailable or fails,
   keep the task resumable and route the condition through recovery or one
   concrete user question; otherwise wait only for bound children.
   A failed targeted wait is terminal only when the host explicitly proves the
   exact persisted child is unavailable. The lifecycle hook then records the
   same resultless-stop recovery state; inspect once and submit its exact
   failed result before Cortex returns any replacement dispatch. Never treat a
   timeout, transport/generic error, ambiguous multi-target error, or an error
   for another child as proof that a worker ended, and never quote raw host
   error text into task state or chat.
4. Workers do not call lifecycle operations. A worker first reads only its
   exact briefing path, confirms the file is
   read-only and its SHA-256 equals `briefing_digest`, and stops on any
   mismatch. That path, plus only the immutable user-intent and optional
   compiled-plan paths with their exact SHA-256 values supplied in the same
   bootstrap, are the direct-read exceptions below the host-private Cortex
   state root. A compiled plan is a full immutable artifact; a briefing may
   carry its complete content or an exact ref/count/digest index, but no
   backend size rule may force omission. Never list or inspect
   the directory, mutable state, baselines, delegation packages, another
   briefing, or result artifacts. If the
   host filesystem read says this exact path is missing or unreadable, the
   worker may call `read_dispatch_briefing` with the exact project root,
   task id, attempt id, profile, dispatch ref, and digest from the bootstrap.
   An incomplete bounded response may continue only with its returned cursor.
   That scoped fallback returns only the same validated briefing and grants no
   directory or ledger access. Values above the chunk bound are normalized and
   continued with the returned cursor. If it returns a caller/schema diagnostic
   or `retryable=true`, correct the named field and retry the same tool on this
   attempt. A blocked/failed result is durable evidence for server-owned corrective recovery, never a Cortex stop.
   After reviewing it, the worker relies on the server-owned briefing receipt;
   it does not author a digest or evidence marker.
   `complete_attempt` verifies the canonical receipt and current artifact state.
   Read-only workers must select non-writing verification modes before running
   commands: use `PYTHONDONTWRITEBYTECODE=1` for Python, disable pytest and
   equivalent test/build caches, and skip any check that requires cleanup.
   They must never create an artifact and then use `rm`, `git clean`, or a
   cleanup script. The result validator records ordinary source deltas as
   concurrency evidence and rejects claimed `changed_files`; it retains only
   manifest-recognized cross-language ephemeral outputs (conventional generated
   directories/roots/files, virtual environments, recognized build-output
   directories, and bytecode suffixes), including matching conventional paths
   listed in `.gitignore`. Arbitrary `.gitignore` outputs and unrecognized
   generated/cache/coverage artifacts remain failures.
   Predecessor results remain accessible only through scoped
   `read_worker_result`. While the wave is active, the coordinator is in
   `waiting_workers` with `output_policy="silent"`: repeated wait timeouts
   produce no heartbeat or status commentary. Visible output is limited to a
   worker question, worker completion/failure, or a task-relevant question or
   explicit safety/authorization boundary.

   Before any project operation or non-Cortex tool, bootstrap validation is
   mandatory. Validate the complete immutable briefing and every applicable
   acceptance/verification item, supplied predecessor result reference, and
   required gate-evidence input is present and readable. If a Cortex-owned
   input is missing, unreadable, incomplete, or mismatched, do not substitute
   it or begin project work. Record every missing input as an internal
   evidence finding and return `orchestrator_advice`; the coordinator routes
   the exact server-derived corrective dispatch or another worker. Do not ask
   the user, create a durable question, or remain idle for governance,
   briefing, receipt, ledger, gate, or other Cortex evidence. A durable
   `worker_question(action="ask")` is reserved for an explicit task
   requirement, scope, acceptance, or external/destructive authorization
   decision; only that question path resumes the same child with
   `followup_task` and `poll`.

   Any worker may call `worker_question` when repository evidence cannot
   resolve a material user decision. Before pausing, it collects every
   currently known material decision: use `action="ask_batch"` when there is
   more than one, and `action="ask"` only when exactly one is known. It returns
   a compact `question_ref`; the worker sends that ref plus a complete decision handoff
   through the native parent. The handoff states why input is needed,
   every full self-contained question, every concrete outcome-based option and
   its material trade-offs, and the worker's recommendation. Placeholder copy
   such as `Option 1`, `Decision 1`, `Recommended option`, or translated
   equivalents is forbidden. The worker publishes no result projection and finishes its
   native turn into an idle/resumable state rather than busy-waiting. The
   coordinator calls
   `manage_orchestration(task_ref="<current task ref>", intent="question",
   payload={"question_ref": "<exact ref>"})` exactly once; Cortex owns
   task/principal/thread resolution and returns a complete
`cortex/chat-interaction/v1` with a bounded `user_view` and an internal
receipt. Render only `user_view` as the final ordinary
   assistant message in
   the user's language: one decision, its concrete options/trade-offs, and the
   visibly labelled recommendation. For a non-English task, the question
   scaffolding (`why_it_matters`, recommendation, and `next_step`) must use
   that same language; canonical worker rationale remains internal. Never copy
   keys, IDs, cursors, tools, or
   the remaining batch into user prose. End the turn without calling any UI/input/approval/elicitation
   tool. A batch is rendered sequentially as
ordinary chat interactions; every user answer is checkpointed before the next
question is shown. The next
ordinary user message supplies the answer for the same interaction ref; do
not guess an internal identity or infer an answer from silence. After the
answer is durably recorded,
resume the exact same native worker through `followup_task`; that worker
calls `worker_question(action="poll")` with the same attempt and ref before
continuing. Never replace the worker or advance the wave for a question.
   During work, checkpoint material facts with `record_attempt_event` and
   finish exactly one attempt with `complete_attempt`. The payload contains
   only semantic `status`, `summary`, `findings`, `decisions_needed`, and
   `unresolved`. Invalid payloads are
   corrected and retried on the same attempt; finalization/projection errors
   never authorize a replacement worker. Cortex derives identity, timestamps,
   changed files, checks, receipts, and evidence from canonical state and
   server observations, then exposes the result ref and scoped human/handoff
   projections. The worker returns only `ATTEMPT_COMPLETED attempt_result_ref=<generated id>`
   plus at most two summary sentences and never pastes a generated projection.
   Generated content is redacted by the server. It is a projection,
   not worker-authored authority. When predecessor handoffs are supplied, a
   successful scoped `read_worker_result` creates the server-owned receipt.
   A successor worker reads each supplied predecessor ref before repository
   work through `read_worker_result`, passing the exact project root, task ref,
   attempt id, profile, and supplied result ref from its generated briefing.
   Cortex rejects attempts to read an ungranted result. This scoped read does
   not authorize coordinator lifecycle calls or user-facing projection links.
   An ordinary completed semantic result may use `unresolved` only for
   concrete material open items required by a successor handoff. Closure
   verifier gates (`review`, `governance_activation`, `governance_close`, and
   `close`) that declare pass MUST use `unresolved=[]`; any concrete unresolved finding
   keeps that closure outcome from passing. A blocked result may use
   `unresolved` only for concrete material findings or unanswered required
   decisions. Residual risk, omitted/environment checks, retrospective notes,
   uncertainty, and placeholder `none` entries belong in `summary`, `claims`,
   or AttemptEvents and never in `unresolved`.
   Review, governance activation, governance close, and final close workers
   publish the same semantic AttemptResult as every other worker. Findings,
   decisions, material findings, verification claims, and changed paths
   are represented by their documented AttemptResult fields and AttemptEvents;
   server observations and human/handoff projections are added by
   Cortex. A corrective worker may result a changed artifact but cannot resolve
   an inherited finding. Only a fresh rerun of the gate that opened the exact
   fingerprint, with its immutable origin result and a separate server-bound
   corrective result in the scoped handoff and an active server-recorded
   rework route for the current semantic task revision, may result that finding
   as `resolved`. Repeating an open fingerprint adds evidence but never
   replaces its original verifier authority.
   For C2/C3 close attempts, each executed test or verification result also
   requires a non-empty concrete summary of observed output or behavior. Concise
   summaries are valid; no arbitrary word count applies, and completion
   assertions without observed output or behavior are rejected.
   Read-only profiles run in a host-enforced read-only sandbox. Source changes
   that appear in their shared checkout during the attempt are recorded as
   concurrent workspace evidence and are not attributed to that worker.
   `changed_files` must still be empty. Manifest-recognized ephemeral outputs
   are retained for read-only validation: conventional generated
   directories/roots/files, virtual environments, recognized build-output
   directories, and bytecode suffixes, including matching conventional paths
   listed in `.gitignore`. Arbitrary `.gitignore` outputs and unrecognized
   generated, cache, coverage, or snapshot artifacts remain a hard result
   failure.
   Every invalid semantic result returns field paths and fixes. Correct every
   named field and retry on the same task and attempt. `complete_attempt`
   atomically revalidates current state before persistence. Stop only for a
   non-retryable error or when exact attempt identity is unavailable.
   `followup_task` resumes the same addressable native worker for an answered
   durable question or an explicit active steer. Active steer is recorded as a
   new task revision and delivered to the existing `host_agent_id`; it does
   not create an attempt or a replacement worker. If native worker completion contains a
   `complete_attempt` error or anything other than `ATTEMPT_COMPLETED` or
   `QUESTION_RECORDED`, do not send a corrective follow-up: `SubagentStop` has
   already classified that attempt. Call
   `manage_orchestration(intent="inspect")` once, then consume a recovered
   result, route the durable question, or submit the exact failed result that
   inspect returns. Only a newly returned top-level dispatch authorizes rework
   after a worker is no longer resumable.
   QA, review, implementation, and corrective pipeline rework are unbounded while acceptance criteria,
   required verification, or blocking canonical findings
   remain unresolved. Failure counts remain durable audit/routing evidence and
   never block a new corrective dispatch. Cortex raises reasoning effort to
   `high` after one unresolved attempt, `xhigh` after two, and `max` after three
   or more; after two it also routes eligible ordinary work to Terra unless the
   user explicitly selected a model. `next_strategy` and replanning remain
   optional evidence-backed improvements, not retry permits. Continue until
   the defect is fixed or an explicit non-retryable integrity, permission,
   storage, unavailable-identity, or external-authority blocker is proven.
5. After all workers finish, read every ref with `read_worker_result`. A result
   is always rereadable and remains the sole machine-readable AttemptResult
   authority. Large results are returned as the complete immutable artifact
   through the signed cursor; any page size is caller-selected transport
   pagination, never a backend content limit or truncation rule. Never
   guess, substitute, or browse a separate projection path. Then evaluate
   the results against the pipeline, then call `continue_orchestration` exactly
   once with `project_root`, the opaque `task_ref` and relative `step` from the
   prior response, and all results keyed by `attempt_result_ref`. A single-worker result may omit
   its slot; parallel results repeat only the returned integer `worker` slot.
   Omit status for success; non-success requires normalized `status`, `reason`, and
   the exact `dispatch_ref` from that stopped worker's returned dispatch (or
   from `context_handoff.stopped_workers`). It omits `attempt_result_ref`. This binds a
   failure to one attempt, so a duplicate stale failure can never be applied to
   its replacement. A native `spawn_agent`/`wait` sequence, a child message, a
   local close, or a result ref alone is never completion evidence. The
   successful `continue_orchestration` response is the required server-derived
   continuation/terminal audit for that wave. Until that audit returns, do not
   present completion, treat a result as consumed, or close its child as
   consumed. Until all workers finish, remain idle and perform
   no project operation. A `SubagentStop` after `complete_attempt` leaves the
   attempt completion-pending while the server materializes its projection,
   not active and not resumable. The coordinator must explicitly choose exactly one
   returned `attempt_result_ref` and submit it through `continue_orchestration`; Cortex
   verifies the ref against the exact task, gate, attempt, and current revision
   before consumption. Never auto-select an AttemptResult, silently approve it,
   respawn the stopped child, or call `followup_task` for this state. Missing,
   stale, already-consumed, or mismatched refs fail closed and require recovery;
   multiple valid refs remain audit-visible until the coordinator chooses one. A
   stop on an open durable question remains resumable; any other stop is
   durably failed and must be submitted as a non-success result for canonical
   rework. Never wait on or respawn a stopped child directly.

   Native agent slots have their own lifecycle. Before every new native
   `spawn_agent`, use `close_agent` when available to release each known
   completed child only after its canonical result was read and the successful
   server continuation/terminal audit consumed it, or after its exact failed result Cortex already accepted. Never close a running child or
   one paused on a durable question. If recovery may have missed a terminal
   child, use `list_agents` defensively and apply the same eligibility rule.
   After that server audit and when no question or follow-up remains, close
   that exact completed native child; the ledger and result store are then
   authoritative.
6. Repeat one continue per completed wave. Finish only when `outcome` is
   `completed`; Cortex has then reconciled results, evidence, documentation,
   close verification, the manifest, and handoff. Only then may the
   coordinator present a final result to the user.

### Recovery after context reset or compaction

If the host compacts or clears the conversation, or resumes the task with a new context
window, or the coordinator no longer has the exact Cortex protocol in active
context, preserve the opaque `task_ref` and call
`manage_orchestration(task_ref="<preserved ref>", intent="inspect")` exactly once for that task. Use the
returned `context_handoff`, current pipeline, result refs, and relative step
as the authoritative recovery snapshot. Invoke only top-level inspect
`dispatches` that correspond to `context_handoff.pending_dispatches`; the
handoff itself is descriptive, not spawn authority. Never respawn entries in
`active_workers`; wait only on their exact persisted `host_agent_id` values.
The documented `SubagentStart` hook binds each native child id/model to the
exact returned dispatch identity before project work (`agent_type` is
`default` for dynamic workers), so inspect can distinguish those states.
When such an exact targeted wait returns a host identity-unavailable proof,
the `PostToolUse` hook retires only that bound child as an AttemptResultless terminal
stop. Inspect then exposes it in `stopped_workers` for one failed continuation;
generic or ambiguous wait failures leave it active and do not authorize a
replacement.
If a running attempt has no child id, fail closed instead of spawning or
waiting without a target. Do not call `start_orchestration`
again, replay completed dispatches, or reconstruct state from a raw
transcript. After rehydration, continue the existing task using only the
returned AttemptResult refs and completion summary.

Inspect is a read-only snapshot, not an implicit attempt repair. It never
expires a lease, invalidates an unusable stopped-result receipt, or creates a
missing Markdown projection merely to render status. When its
`lifecycle_recovery` result says recovery is required, use only the exact
bounded server-returned recovery action; do not guess an attempt identity,
repeat a dispatch, or treat the original inspect as permission to mutate state.

### Explicit user-requested post-plan approval

`task.plan_approval` accepts `auto` or `required` and defaults to `auto` for
every complexity. `required` is used only when the user explicitly requested
post-plan approval; governance, Planner, risk, and review recommendations never
request it on the user's behalf. A user-requested required plan must be its own
wave. Once that plan completes, the lifecycle result is
`awaiting_plan_approval` with no successor dispatch and a machine-readable
`plan_review` containing the objective, complete work packages and microtasks,
paths, dependencies, verification, material risks, `attempt_result_ref`, and
`remaining_phases`. The coordinator reads the result, then calls
`manage_orchestration(intent="plan_approval", payload={"decision":"prompt"})`.
Cortex returns `cortex/chat-interaction/v1` with `user_view` and `internal`.
Render only `user_view`: a 3–5 step human summary, one four-way approval
question (approve with recommendations, approve without recommendations,
revise, or cancel), the concrete corrective actions, and the single planner
recommendation.
Do not expose work-package paths, dependencies, result/request IDs, or tool
instructions. Do not call a UI, input, approval, or elicitation tool. End the
turn and wait. Submit the user's next message with the exact `interaction_ref`
as `request_id`: approval uses `approve_with_recommendations` or
`approve_without_recommendations`, explicit cancellation uses `cancel`, and any
requested change uses `revise` with the exact feedback text.
Planner then reruns before another approval hold. Silence never approves. This gate is
separate from `worker_question`: material questions are resolved through that
lifecycle during planning rather than through a duplicate approval question.

The Planner may attach a separate `planning` object to its semantic
`complete_attempt` payload. It contains exactly `overview` and `work_packages`; the
strict AttemptResult contract remains unchanged. Each package
has `id`, `title`, `objective`, optional `allowed_paths`/`depends_on`, and
non-empty microtasks; `profile` is forbidden at package level. Each microtask
requires `id`, `title`, `objective`, an explicit `profile`, narrow non-broad
`allowed_paths`, non-empty `acceptance_criteria`, and non-empty `verification`,
with optional `depends_on`. Every package/microtask `id`, dependency, and
`requirement_coverage.plan_refs` value must be a unique lowercase safe
identifier matching `[a-z0-9_-]+` and must reference an existing package or
microtask; never emit display labels such as `MT-01`.
Cortex requires microtask IDs to be globally unique across the plan, allows
`depends_on` to reference microtasks in another work package, rejects unknown
references, and validates the combined microtask dependency graph as acyclic.
Each package and microtask also carries explicit tracker fields: `status`
(`pending`, `ready`, `running`, `blocked`, `completed`, or `skipped`), positive
`order`, and non-empty `gates`. Omitted values default to `pending`, source
order, and `implementation`; the compiled plan preserves these fields as the
canonical worker-visible tracker snapshot.
It enforces 32 packages, 32 microtasks per package, and 128 total microtasks.
The Planner remains read-only; Cortex materializes immutable, revision-scoped
host-private `tasks/<task>/planning/revisions/plan-<result-ref>/overview.md`
and `packages/<id>.json` artifacts. The SQLite task document
`planning_current` is the sole current-plan pointer; there are no
`planning/manifest.json` or `planning/overview.md` latest aliases.
`plan_review` exposes compact
`planning_artifacts` for approval. Treat this as a durable catalog for
ownership/dependency-aware scheduling. After approval, Cortex topologically
compiles all microtasks into an immutable `compiled_plan_unit` artifact and
dispatches that exact executable contract. It never substitutes a generic
implementation objective or broad `.` path.

If `complete_attempt` rejects a planner payload, Cortex stores the complete
rejected draft and returns its `base_payload_digest` plus all independent,
path-addressable diagnostics. The next call on that same attempt is
PATCH-only: send only `base_payload_digest` and a non-empty RFC6902 `patches`
array whose paths target the returned diagnostic paths (or descendants).
Never regenerate or resend the full `planning` object after a validation
error. Fields that passed validation are retained server-side and must be
left out of the repair payload; a patch outside the diagnostic scope is
rejected atomically and does not mutate the draft or create an AttemptResult.

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
dispatches. Its workers receive source-derived handoff and AttemptResult refs
as historical context only; they must revalidate consequential claims
 against current source and tests. `payload.attempt_result_refs` is optional and
bounded to at most 32 source refs; when omitted, Cortex selects a bounded
recent set. If the source task is active, use evidence-based `rework` instead;
`follow_up` rejects active sources.

`read_worker_result` returns the canonical AttemptResult and bounded completion
summary after durable native completion. Repeated reads remain available for
evaluation and never create a separate user-facing artifact. Never guess,
substitute, or use a path to browse unrelated files.

When `continue_orchestration` returns `ready_to_spawn`, invoke only each
returned `dispatch.call` with its exact `dispatch.arguments`, in order. A
generic collaboration spawn, self-authored task name, or replacement child is
not a Cortex dispatch: it cannot bind to or advance the pending attempt. For a
resumable child, keep the existing native target and use `followup_task`; after
its result, the required order is `read_worker_result`, successful
`continue_orchestration`, then `close_agent` for that exact completed child.
Inspect `available_results` exposes an existing derived path only when its
optional projection is already materialized; recovery uses the exact result
ref, and `read_worker_result` remains the publication-eligible link surface.
Required-plan `plan_review` retains its derived path for approval review.

`continue_orchestration` is a one-shot receipt for the exact current wave. On
success, perform only the action authorized by that response: invoke its
returned dispatches, wait for the exact persisted active workers, or stop on a
   terminal `completed` outcome after closing the consumed child. A terminal
   worker `blocked`/`failed` outcome is server-owned recovery evidence: follow
   the returned corrective dispatch for the orchestrator's chosen pipeline and
   do not stop the Cortex task. A Planner is used only when that chosen
   corrective pipeline includes one; the finding never forces a Planner wave.
   Never call `continue_orchestration` again with the same step/results, request
   artifacts, add `future_waves`, or spawn a replacement after that receipt. A
   `retryable=false` task-identity or relative-step diagnostic is reconciled by
   one server-owned inspect/recovery receipt; never expose it as a Cortex block
   or ask the user to repair internal state.

Normal requests never carry caller-generated submission, task, wave, attempt,
principal, thread, host-tool, host-model, or host-effort fields. Internal IDs
and receipts remain durable below the host-private Cortex state root.

## Idempotency and relative references

Start resumes an identical unfinished request automatically. Continue replays
a byte-identical retry for its internal active wave. The relative `step`
distinguishes identical result content used on successive waves without
exposing durable identity. Parallel worker slots are complete, unique, and
validated atomically before task state changes.

Every successful start response says whether it is a replay. Once start
returns dispatches, it is complete: invoke those dispatches and never call
`start_orchestration` again for that `task_ref`, including while translating
or preparing native arguments. A replay returns no dispatches and cannot
authorize a second worker wave. If the first response was lost before native
dispatch, recover still-awaiting requests once through management inspect.

Preserve the `task_ref` returned by a **successful** start and pass it on every
later task-scoped lifecycle and result-read call. Different task contracts can
run concurrently below one project root; the project registry is
lock-serialized and task records remain isolated. An exact duplicate active
start is an idempotent replay. An omitted ref always fails closed with
`task_ref_required`; never inspect, list, infer, or select a project task as
recovery. A failed start without a `task_ref` created no recoverable task.

## Adaptation and recovery

The returned `pipeline.waves` snapshot is the current coordinator-owned plan.
Follow it by default. Pass compact `future_waves` only when the coordinator
decides that verified evidence materially changes work that has not started;
include a concise `reason`; reason prose is audit-only and cannot authorize a
recovery or release a liveness pause. Planner and explorer ownership recommendations are
advisory routing evidence, not an automatic rewrite command. Prefer the
narrowest supported profile and replace a stale route only after that explicit
   decision. `general` is a conservative fallback, not the preferred universal
   writer. The public facade infers rework when `future_waves` reintroduces a
   current or completed phase; the optional `rework` field is only a hint. A
   pending implementation phase is retained as an evidence obligation, while
   the coordinator may narrow dependencies or select another owner. After an
   exhausted closure-rework cycle, ordinary `resume` records the condition and
   returns corrective options; it does not impose a Planner wave or fresh plan
   approval. Before documentation or close dispatch, the runtime reports any
   missing graph as an advisory finding and the coordinator chooses the repair
   owner. `replan_count` is audit history, not a task-wide quota; the retained
   `replan_limit` field never stops a new evidence-backed review/remediation
   cycle. The facade preflights request integrity before recording the current
   gate. A requested inactive gate is never silently substituted with the first
   active gate: `record_gate` returns the retryable, non-mutating `gate_mismatch`
   result with the requested gate and active-gate list so the coordinator can
   retry exactly the intended transition. A partial failure that left an active
   current gate without a live or pending dispatch is returned as corrective
   advice; the coordinator may choose a Planner or another owner. Use
`manage_orchestration` for `inspect`, `recover_inspect`, `resume`,
`deactivate`, `lane`, `resource`, or `question`; these intents do not belong
in normal wave calls.
Follow recoverable diagnostics and never fall back to private tools.

A materially identical no-progress signature is durable routing evidence, not
a pause or retry budget. Unpaused siblings remain executable and the
coordinator may dispatch a corrective owner for the affected gate immediately.
The response can recommend a materially different pipeline, strategy, or
verification contract, but this recommendation never authorizes or forbids a
future wave. Infrastructure/environment findings name a class-matched
remediation. If multiple gates have findings, `manage_orchestration(intent="resume")`
may name the intended gate with `payload.rework`; no other gate is implicitly
released. A partial baseline or current manifest remains diagnostic evidence
and is routed to the coordinator's chosen reconciliation or verification owner.

The question intent accepts only the worker's exact `question_ref` on the
normal path and resolves all durable identity internally. It returns a
`cortex/chat-interaction/v1` projection. Render that projection completely in
the user's language as one ordinary **final assistant message**, then end the
turn without calling any UI, input, approval, or elicitation tool. The user's
next ordinary message resumes the same durable task: preserve its exact text,
record it against the same `interaction_ref`, and only then resume the exact
same worker. The coordinator may pass
`localized_question`, `localized_header`, `localized_options`, and
`localized_custom_label` as transient user-language labels; the stored
question remains canonical English. For a response with
`outcome="awaiting_translation"`, use its returned `translation_request`
directly: a single question uses the original `answer` plus its translated
`answer_en`; a batch supplies only the listed `canonical_answers`. Do not
inspect plugin sources or infer alternative fields. Answers preserve the
user's original value and language and require `answer_en` for localized free
text or a choice's free-form custom response before the worker receives the
canonical English answer. Every choice question renders that optional custom
field beside its stable options. Workers may use
`worker_question(action="ask_batch")` with 1–32 stable questions and poll the
same `batch_ref` with `action="poll_batch"`; the coordinator renders one
currently unanswered decision at a time in a natural-facing message and
Cortex durably checkpoints that answer before showing the next item. For a non-English
batch, `localized_questions` is an ordered display
projection only: preserve the canonical order when possible, but never invent
or reconstruct canonical `question_key` or `option_id` values. Cortex maps
each projection by its exact canonical key only when the complete batch
preserves all keys; otherwise it maps positions and ignores display IDs. Each
projection uses `localized_question`, `localized_header`, `localized_options`,
and optional `localized_custom_label`; the obsolete aliases `question`,
`header`, `options`, and `custom_label` mean the same thing. Every localized
question must state the concrete decision, and every option must name its
outcome or trade-off; generic numbered or recommended/alternative placeholders
are rejected. Every question also requires `recommendation` plus exact
`recommended_option_ids`, or `recommended_answer` for free text. The user-facing
message must visibly label which answer the LLM recommends and explain why; a
neutral choice still needs an explicit recommendation rationale. A task
revision supersedes an unresolved batch rather than resuming stale user intent.
Every worker result automatically includes the task-wide canonical
`resolved_user_decisions` snapshot. A successor must review it as durable user
authority and must never ask a materially equivalent question merely because
the wording, key, phase, or attempt changed; only an explicit current user
change reopens that decision.
Every
worker classifies unknowns as repository-resolvable, low-impact reversible, or
material user decisions. Only the last class pauses through `worker_question`;
existing code is current-state evidence, not evidence of desired product
intent. Cortex rejects `complete_attempt` and `continue_orchestration` while a
blocking question remains open, rejects any non-empty final result question
list, and requires an answered blocking question before decision-bearing
phases can complete when deterministic intent preflight marks a short product
surface request as underspecified. Ordinary chat is the only user decision
surface: after the detailed question message the task must stop until the user
answers. Silence never authorizes a default answer.

The Question Firewall is the authoritative boundary for this surface. Only
task requirement, scope, acceptance, product, or explicit
external/destructive-authorization decisions may become a durable user
question. Cortex policy, governance, gate, planner, retry, worker/profile,
dispatch, ledger, evidence, receipt, lifecycle, and recovery conditions are
never user questions. If `context.decision_scope` is supplied, it must name a
task scope (`requirement`, `scope`, `acceptance`, `product`,
`external_authorization`, or `destructive_authorization`) for a user pause;
internal scopes route to the coordinator as `orchestrator_advice`. An advice
response is recoverable and must be recorded and delegated without creating a
question document or setting `requires_user_decision=true`.

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
it fails closed while active workers exist and removes only host-private Cortex
state, never project source or documentation.

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
`xhigh`, and `max`; never request another value. Automatic `max` covers C3
adaptive Luna work and repeated unresolved corrective failures. A coordinator may explicitly override an
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
every message, tool argument, result, durable question, handoff, and native
final response. Durable worker questions remain English; the coordinator may
pass `localized_question`, `localized_header`, `localized_options`, and
`localized_custom_label` as user-language chat projections without altering the
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
result. The coordinator attaches those paths through later-wave
`context_files`; downstream workers also re-check the indexes for missed
cross-feature dependencies. Documentation is a navigation layer and prior,
not authority: workers confirm consequential claims in current source, tests,
schemas, or executable configuration. Every worker result must include one
`Knowledge reviewed:` evidence entry naming both available indexes and every
additional knowledge page actually used. The result tool rejects an omitted
index receipt.

Canonical phases are `scope`, `plan`, `discover`, `architecture`,
`database_architecture`, `implementation`, `qa`, `security`, `performance`,
`accessibility`, `ux`, `review`, `documentation`, and `close`. One phase may
appear in only one wave; multiple owners for a phase share that wave. Generic
`verification` maps to `qa`, while `build_verification` and
`final_verification` map to `close`.

Every result remains strict-JSON-, redaction-, path-, lifecycle-, and
receipt-checked. Canonical result/event/question/answer/briefing/artifact
content has no backend byte or character admission quota and is never silently
truncated. Cortex fails closed on root or symlink violations, stale steps,
invalid slots, changed retries, missing sections, invalid rework, failed close
verification, manifest mismatch, incomplete predecessor or knowledge-index
receipt, or handoff context that is malformed, incomplete, or unverifiable. It
never silently drops an older result; narrow the dependency set with
`depends_on` only when the coordinator intentionally scopes the worker's
evidence.

## Durable artifacts

Every call supplies its exact absolute `project_root`. Runtime state stays in
the host-private default `~/.codex/cortex/projects/p-<sha256>/` root (or a
private, outside-workspace `CORTEX_HOST_STATE_DIR` override) using the
canonical `cortex/v8` ledger. `CORTEX_ROOT`, `/tmp` fallback, and symlink
traversal remain forbidden. A old project-local `.codex/cortex` database is
moved only by same-filesystem atomic rename after secure database/split-state
validation; unsafe or cross-filesystem old state fails closed.
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
results, or sensitive operational detail in task inputs, worker prompts,
questions, results, handoffs, logs, or user-visible summaries. Redaction is a
defense in depth measure, not permission to transmit sensitive input.

Before completion, run the smallest non-destructive verification set that
proves the affected acceptance and verification contract, then broaden checks
in proportion to risk. Read-only workers select non-writing modes before they
run: disable bytecode and test/build caches and skip checks that require
cleanup. Never create an artifact and then delete it to simulate read-only
verification. State every unrun required check, environmental limitation, and
remaining uncertainty plainly in summary/claims rather than `unresolved` unless
   it is a concrete unresolved finding. Current source, tests, schemas, and executable
configuration outrank generated documentation.

## Private tool-error diagnostics

Cortex appends raised MCP exceptions and obsolete error-shaped tool results as
JSONL to `~/.codex/logs/cortex-tool-errors.jsonl`, where `~` is the home of the
user running the MCP process. This is private per-user diagnostic data, not the
project ledger. The writer keeps the file at or below 10 MiB by dropping the
oldest complete records and retaining the newest complete records before each
append. Expected public validation and recovery responses with `ok: false` are
not exceptions and are not written to this log.

Records contain bounded correlation metadata such as timestamp, method, tool,
error type, `chat_session_id`/`thread_id`, request id, supplied durable ids,
and a value-free input shape summary. They never retain tool argument values,
result bodies, question text, or user-authored content. The parent
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
