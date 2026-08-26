---
name: cortex-control
description: Internal Cortex v11 runtime protocol. Load only after cortex:orchestrator has been explicitly activated. Never select directly for an ordinary task.
---

# Cortex Control v11

Cortex is an explicit opt-in coordination protocol. The coordinator calls public
Cortex tools, issues only returned native dispatches, waits, reads canonical
results, follows the returned lifecycle, and delivers the verified terminal
result. Repository investigation, editing, testing, and review belong to workers.

## Schema authority and response handling

The active public MCP registry is the sole authority for call and response
shapes. Skills and prompts define only the lifecycle below; they never repeat
schemas, argument templates, aliases, or cursor formats. Before each call,
follow the current registry and the immediately preceding public response.
For `ok=false`, apply only its public diagnostics/recovery: retry the same
operation only when explicitly retryable and unmutated, change only allowed
locations, and copy opaque values byte-for-byte. Otherwise stop the affected
route and fail closed. Never inspect source, caches, logs, ledger, session,
environment, or hidden paths to recover missing protocol data. For every
growing read, follow only the server-issued continuation until completion;
never choose a page size or derive a cursor.

## Public tool catalog

The active server `tools/list` response is authoritative and physically filters
tools by audience. Use this catalog only to choose the semantic action; use the
active registry for every call shape.

The generated catalog below is a source-owned projection of the canonical
registry, partitioned by coordinator and worker audience.

<!-- BEGIN GENERATED CORTEX TOOL CATALOG -->
### Coordinator tools

| Tool | When to use |
| --- | --- |
| `start_orchestration` | Use to start orchestration. |
| `continue_orchestration` | Use to continue orchestration. |
| `inspect_orchestration` | Use to inspect orchestration. |
| `inspect_orchestration_recovery` | Use to inspect orchestration recovery. |
| `recover_blocked_orchestration` | Use to recover blocked orchestration. |
| `resume_orchestration` | Use to resume orchestration. |
| `stop_orchestration` | Use to stop orchestration. |
| `show_orchestration_question` | Use to show orchestration question. |
| `answer_orchestration_question` | Use to answer orchestration question. |
| `read_plan_approval_prompt` | Use to read plan approval prompt. |
| `decide_plan_approval` | Use to decide plan approval. |
| `revise_plan` | Use to revise plan. |
| `revise_future_pipeline` | Use to revise future pipeline. |
| `append_rework_wave` | Use to append rework wave. |
| `start_follow_up` | Use to start follow up. |
| `steer_orchestration` | Use to steer orchestration. |
| `start_auxiliary_worker` | Use to start auxiliary worker. |
| `list_task_artifacts` | Use to list task artifacts. |
| `read_task_artifact_metadata` | Use to read task artifact metadata. |
| `read_task_artifact` | Use to read task artifact. |
| `create_orchestration_lane` | Use to create orchestration lane. |
| `inspect_orchestration_lane` | Use to inspect orchestration lane. |
| `claim_orchestration_lane` | Use to claim orchestration lane. |
| `release_orchestration_lane` | Use to release orchestration lane. |
| `retire_orchestration_lane` | Use to retire orchestration lane. |
| `bind_orchestration_lane` | Use to bind orchestration lane. |
| `materialize_orchestration_lane` | Use to materialize orchestration lane. |
| `reconcile_orchestration_lane` | Use to reconcile orchestration lane. |
| `claim_orchestration_resource` | Use to claim orchestration resource. |
| `release_orchestration_resource` | Use to release orchestration resource. |
| `lock_orchestration_resource` | Use to lock orchestration resource. |
| `unlock_orchestration_resource` | Use to unlock orchestration resource. |
| `read_orchestration_lifecycle` | Use to read orchestration lifecycle. |
| `finalize_bootstrap_failure` | Use to finalize bootstrap failure. |
| `finalize_worker_failure` | Use to finalize worker failure. |
| `inspect_governance_initiative` | Use to inspect governance initiative. |
| `link_governance_task` | Use to link governance task. |
| `add_governance_dependency` | Use to add governance dependency. |
| `transition_governance_initiative` | Use to transition governance initiative. |
| `create_task_governance_record` | Use to create task governance record. |
| `list_task_governance_records` | Use to list task governance records. |
| `read_task_governance_snapshot` | Use to read task governance snapshot. |
| `create_initiative_governance_record` | Use to create initiative governance record. |
| `list_initiative_governance_records` | Use to list initiative governance records. |
| `read_initiative_governance_snapshot` | Use to read initiative governance snapshot. |
| `create_initiative_task_governance_record` | Use to create initiative task governance record. |
| `list_initiative_task_governance_records` | Use to list initiative task governance records. |
| `read_initiative_task_governance_snapshot` | Use to read initiative task governance snapshot. |
| `evaluate_governance_promotion` | Use to evaluate governance promotion. |
| `inspect_governance_promotion` | Use to inspect governance promotion. |
| `read_worker_wave` | Use to read worker wave. |

### Worker tools

| Tool | When to use |
| --- | --- |
| `ask_worker_question` | Use to ask worker question. |
| `poll_worker_question` | Use to poll worker question. |
| `record_attempt_event` | Use to record attempt event. |
| `record_worker_finding` | Use to record worker finding. |
| `submit_attempt` | Use to submit attempt. |
| `submit_governance_closure` | Use to submit governance closure. |
| `repair_attempt` | Use to repair attempt. |
| `read_dispatch_briefing` | Use to read dispatch briefing. |
| `refresh_worker_context` | Use to refresh worker context. |
| `list_worker_reports` | Use to list worker reports. |
| `read_predecessor_result` | Use to read predecessor result. |
<!-- END GENERATED CORTEX TOOL CATALOG -->

## Authority boundaries

The server issues separate opaque coordinator and worker authority. Preserve
each value byte-for-byte only in calls authorized for that lifecycle; never
expose, infer, reconstruct, or exchange it. A worker verifies that its native
dispatch supplied authority before any Cortex call or project access. Missing
authority means no project access and only public fail-closed recovery. If the
first call reports that trusted native spawn observation is pending, retry that
same call with bounded backoff to the returned finite deadline; never switch
operations or spawn a replacement. An exact successful retry clears that
transient condition. Child prose is status only; a coordinator follows only
server-returned recovery.

## Lifecycle

The model authors the current-schema waves; Cortex validates them and returns
exact native V2 dispatches. `auto` is the normal governance choice; use
`required` or `minimal` only when the task calls for it, and never infer or
silently rewrite a user choice.

1. Start one explicitly activated task, invoke every returned `spawn_agent`
   dispatch with its complete arguments unchanged, preserve each exact child
   identifier returned by the host, then immediately invoke `wait_agent` for
   those exact bound children. `read_worker_wave` is forbidden until
   `wait_agent` reports every exact bound child terminal. Allow parallel wave
   members. The worker copies the exact `CORTEX_DISPATCH_REF` first line from
   its unchanged native message into the required `read_dispatch_briefing`
   authority field; it never infers or rewrites that value. A Terra or Sol model override is mandatory when
   returned; Luna intentionally omits it and uses the verified configured
   native default.
2. Each worker reads its complete immutable briefing before project access.
   The host binds its first authorized Cortex call to the matching native child
   in the root session; the model never reconstructs that identity.
3. Use `wait_agent` with a 300-second timeout for progress and match its report
   to the exact child identifiers returned by the preceding native dispatches.
   `No agents completed yet`, a timeout, an empty completion set, `pendingInit`,
   `running`, or any still-working child is nonterminal: immediately wait again
   for the same exact child identifiers and call no Cortex read or lifecycle
   tool. The only native terminal host statuses are `interrupted`, `completed`,
   `errored`, `shutdown`, and `notFound`. Only after every exact bound child
   reports one of them may
   the coordinator call `read_worker_wave`; the backend independently verifies
   the trusted terminal observation.
   If `inspect_orchestration` or that read returns `continue`, call
   `continue_orchestration` exactly once with the unchanged coordinator
   authority; do not wait, reread, or create a new child first.
   After a host resume, inspect once before waiting on any retained child. If
   the final page returns `resume_orchestration`, the server has authenticated
   an exclusive new Codex process epoch and proved the prior host dead. Call
   `resume_orchestration` exactly once before any wait, wave read,
   continuation, or native child creation. Invoke only the returned new-child
   replacements, then enter the ordinary exact-child wait loop. Never reuse or
   wait for a prior-epoch child. An unavailable or ambiguous epoch fails
   closed; do not infer recovery from `list_agents`, prose, process listings,
   or a SessionStart label alone.
4. A worker may publish one arbitrary-Unicode question only for a real user
   decision. Internal technical conditions use server-owned recovery and do
   not become user questions. Publishing ends the native turn. Present it
   completely and record the complete answer. When the server confirms the
   original bound child is still live, resume that same child in a new native
   turn. When the server confirms that child's authenticated native host was
   retired, invoke only the exact new-child replacement dispatch returned
   after the answer; never address, wait for, or follow up the retired child.
   In both cases the authorized worker uses `poll_worker_question` to read the
   same canonical durable answer before finishing. Ordinary workers then call
   `submit_attempt`; a `governance_close` worker calls only
   `submit_governance_closure`. Before that closure submission, it consumes the
   complete `list_worker_reports` catalog, reads every entry marked required
   through `read_predecessor_result`, and consumes every page of each required
   report. Optional entries are selected only when their metadata is relevant
   and never become completion blockers; briefing projections alone do not
   create the required server read receipts. If the server returns
   `read_required_context_then_retry`, complete those authorized reads and then
   retry the same semantic submission unchanged. A successful
   answer receipt directing `resume_bound_worker` returns one exact native
   same-child `followup_task`; invoke it unchanged first, then immediately use
   `wait_agent` for that exact bound child, and only after its terminal report
   perform a server lifecycle/wave re-read.
   Child binding and authorization remain server-side. Never call lifecycle
   continuation between the answer receipt and that completed-wave read. Never
   recreate or replace the exchange.
5. `SubagentStop` plus a canonical terminal result is the completion gate. The
   backend keeps wave reads/continuations unavailable until every bound child
   has both. This is trusted same-user local observation, not cryptographic
   proof; unknown or unverifiable hook state fails closed.
   If an early wave read directs `wait_for_bound_workers`, invoke another
   native `wait_agent` cycle for the exact bound children, and do not re-read
   the server lifecycle/wave state before every child has a terminal status.
   Cortex retains child binding and authorization
   server-side. This action is legal while the bound child has no trusted Stop
   or after the server has durably issued its one same-child resume instruction
   and is awaiting that child's exact dispatch-authorized answer poll or
   result. A follow-up turn need not emit another `SubagentStart`; never send a
   second `followup_task`.
   After a trusted result-less Stop, follow the wave read action literally:
   `obtain_user_decision` surfaces the recorded durable question;
   `resume_bound_worker` invokes the exact returned same-child `followup_task`
   once unchanged, then invokes `wait_agent` for that exact bound child and
   re-reads the server lifecycle/wave state only after its terminal report;
   `invoke_dispatches` spawns every exact
   server-issued selected-route replacement. Never wait or resume a
   terminalized child, and never infer one action from child prose.
6. Read the completed wave. If its evidence requires a material change, make
   one decision for that evidence frontier. Use `revise_future_pipeline` only
   to replace unexecuted future work. When a completed canonical result itself
   needs product correction, use `append_rework_wave`; Cortex appends the
   mutating rework and its independent verification without rewriting completed
   history. Never use either operation for transport, host-observation, model,
   or other technical recovery: Cortex owns those replacement routes and
   escalates the exact assignment occurrence through Luna, Terra, and Sol,
   retaining the selected profile when compatible and otherwise resolving an
   operation-capable profile from the canonical profile registry. A
   server-issued technical-recovery worker first consumes every supplied
   source and recovery-chain result through the paginated predecessor-result
   read; compact server context states the exact deficit while complete prior
   reports remain referenced instead of copied into the native message. One
   eligible stopped worker may receive one server-issued same-child supplement
   turn before a new-child fallback. Exact replay is idempotent;
   stale/conflicting or executing/completed-wave rewrites are
   rejected. Spawn every returned worker, wait, require Stop plus canonical
   read, then complete governance closure.
   A governance-close worker never infers freshness from numeric task, plan,
   or ledger revisions. Only the server-returned `complete`/`issues` closure
   basis and the structured `submit_governance_closure` response authorize its
   next action.
   A completed wave report is already the canonical evidence. Its result
   references authorize future-pipeline evidence and predecessor context; they
   are never durable-artifact references. From the returned
   `revise_or_continue` action, call the task-required future revision or normal
   continuation directly. Read a task artifact only when the canonical report
   explicitly supplies a distinct durable-artifact reference.

The model decides whether a recorded answer addresses its question. Child prose
and wait output never authorize lifecycle operations.

`followup_task` is only for same-child durable-answer resume or a
server-returned same-child recovery. It is never a submission-repair or
replacement mechanism, and it can never repair a native model mismatch. Such a
mismatch requires a newly server-issued replacement dispatch or a fail-closed
stop.

## Management and close

Use only current-registry management and governance actions. Read paged cards
through returned continuations; fixed receipts do not paginate. Host metadata
and trusted `SubagentStart`/`SubagentStop` are only the completion prerequisite,
never a public tool or model-visible recovery surface.
Other hook telemetry cannot authorize, dispatch, retire, recover, reconstruct,
or complete a task.

Visible output is limited to a verified result, a task-relevant durable
question, or an explicit safety/authorization boundary. Internal validation,
policy, worker, governance, and telemetry gaps belong in worker reports and
server-directed correction, not user questions.

Deliver a final user answer only after the server reports terminal lifecycle
success, canonical results are fully read, and durable-close evidence authorizes
handoff. At compaction, preserve the private coordinator pair, server-derived
step, pending native identities, and result references in the bounded handoff.
If authority is missing, fail closed and never reconstruct it.

After coordinator compaction, clear, or reset in the same host incarnation,
call `inspect_orchestration` first and consume every page until complete before
waiting, replaying, following up, continuing, resuming, or creating a child.
An exact worker whose authority was already established by its original native
dispatch and complete briefing read may call `refresh_worker_context` after
that same child's context is compacted. It passes no identity or routing
fields, follows only the returned cursor, and waits for `complete=true` before
acting. Initial workers still bootstrap from the native dispatch and briefing;
refresh never discovers a task, mints sibling authority, replaces a worker,
answers a question, or advances governance.
