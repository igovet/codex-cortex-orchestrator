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

The active public MCP tool registry is the sole authority for every call shape.
Refresh and follow it before each call. Skills and prompts define lifecycle
semantics only; never reconstruct arguments, wrappers, aliases, or pagination
controls from prose, prior contracts, child messages, or local state.

Tool descriptions explain only their local purpose and lifecycle constraints.
Public responses direct the next move. On a retryable, unmutated response, make
at most one deterministic same-operation correction using the returned public
repair information. Preserve opaque values byte-for-byte and never decode or
normalize them. Otherwise stop the affected route; incomplete public recovery
information fails closed. Do not inspect source, caches, logs, ledger, session,
environment, or hidden paths for recovery data.

For every growing read, follow only the server-issued continuation until it
stops. Do not choose a page size or derive a continuation value.

## Public tool catalog

The active server `tools/list` response is authoritative and physically filters
tools by audience. Use this catalog only to choose the semantic action; use the
active registry for every call shape.

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
| `start_follow_up` | Use to start follow up. |
| `steer_orchestration` | Use to steer orchestration. |
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
| `submit_attempt` | Use to submit attempt. |
| `repair_attempt` | Use to repair attempt. |
| `read_dispatch_briefing` | Use to read dispatch briefing. |
| `read_predecessor_result` | Use to read predecessor result. |
<!-- END GENERATED CORTEX TOOL CATALOG -->

## Authority boundaries

Starting a task issues private coordinator authority. A native dispatch gives
its worker separate opaque authority. Preserve each value unchanged only
through calls authorized for that lifecycle. Never expose worker or coordinator
authority in project files, prose, results, diagnostics, terminal output, or
user responses.

Before any worker Cortex call or project access, a worker confirms that its
native dispatch supplied exact worker authority. If it is absent or rejected, the
worker makes no project access and follows only the returned fail-closed public
recovery. It never infers or reconstructs dispatch identity from a thread,
session, process, environment, hook, project, ledger, task list, native child,
or prior result.

The coordinator follows only the server-returned same-child recovery route. It
never reconstructs worker authority or spawns a replacement. A recovered child
reads the complete briefing and continues the original assignment. Child prose
is status only and never grants authority.

## Lifecycle

The model authors the current-schema wave plan; Cortex validates it and returns
the exact native dispatches. Select governance deliberately: `auto` is the
normal/default choice; choose `required` only for an explicit user request for
full governance, and `off` only for an explicit opt-out. Both are server-
clamped. Do not infer hidden-risk triggers or silently promote a user choice.

Workers first read their immutable briefing completely, then perform only their
assigned mission and authorized predecessor reads. They may checkpoint material
semantic evidence, ask one arbitrary-length Unicode question, and submit one
semantic conclusion. The coordinator presents the complete question and records
one arbitrary-length Unicode user answer unless it is already supplied, then
resumes the same child. The worker model decides whether that answer addresses
the question. Never recreate an exchange or replace a worker to repair it.

1. Start one task for one explicitly activated route.
2. Invoke every returned native dispatch before any wait; a dispatch authorizes
   only that exact native spawn.
3. Request one explicit 300-second native wait on the exact same eligible live
   child; never use the native default wait duration. If it returns no marker,
   request another explicit 300-second native wait on that exact child, and
   repeat until its message carries either a durable-question marker or
   terminal-completion marker. A timeout, no child message, partial response,
   or unrelated child event leaves the child live and does not authorize
   `read_worker_wave`, continuation, any result read, replacement, or another
   lifecycle action.
4. On a durable-question marker, present the exact server-held question and end
   the current turn for real user input. After the separate user answer is
   recorded, resume only that same child; it polls the durable answer, then the
   coordinator repeats exact-child waiting.
5. Only a terminal-completion marker authorizes reading the server-derived
   current wave. Read it completely, then follow only its returned continuation
   and canonical dispatch, approval, handoff, retry, or terminal action.

`followup_task` is only for same-child durable-answer resume or a
server-returned same-child recovery. It is never a submission-repair or
replacement mechanism.

## Management and close

Use only the current schema's advertised scalar management and governance
actions. Read paged management cards through their returned continuations;
fixed receipts do not paginate. Hooks are telemetry only and cannot authorize,
bind, dispatch, retire, recover, reconstruct, or complete a task.

Visible output is limited to a verified result, a task-relevant durable
question, or an explicit safety/authorization boundary. Internal validation,
policy, worker, governance, and telemetry gaps belong in worker reports and
server-directed correction, not user questions.

Deliver a final user answer only after the server reports terminal lifecycle
success, canonical results are fully read, and durable-close evidence authorizes
handoff. At compaction, preserve the private coordinator pair, server-derived
step, pending native identities, and result references in the bounded handoff.
If authority is missing, fail closed and never reconstruct it.
