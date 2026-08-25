---
name: cortex-control
description: Internal Cortex v11 runtime protocol. Load only after cortex:orchestrator has been explicitly activated. Never select directly for an ordinary task.
---

# Cortex Control v11

Cortex is an explicit opt-in coordination protocol. The coordinator does not
perform project work. It calls the public Cortex tools, issues only returned
native dispatches, waits, reads canonical results, continues the lifecycle, and
communicates the verified terminal outcome. All repository investigation,
editing, testing, and review belong to a worker.

## MCP Cortex response handling

Follow the public structured response, never host state or prose.

- `isError=true` is the MCP tool-execution-error channel. It may still carry a
  Cortex structured domain response (`ok=false`, `error`, `recovery`); process
  that recovery exactly as below.
- `ok=true`: perform only the returned lifecycle action, dispatch, permitted
  wait, continuation, question state, or terminal outcome.
- `ok=false`: this is an expected Cortex domain result. Read only top-level `error` and `recovery`, including their diagnostics, field schemas,
  `allowed_changes`, and opaque repair values.

Recovery kinds are closed. `same_operation` is legal only when
`retryable=true`, `state_mutated=false`, and non-empty `allowed_changes` make
one exact retry deterministic; apply those changes once, then follow the next
response. `repair_patch_only` permits only the returned opaque handle, digest,
and allowed patch paths. `inspect_server_state` permits only the returned
operation and arguments. `terminal_stop` ends task-scoped calls and allows only
explicit terminal cleanup. Never decode, shorten, normalize, reconstruct, or
manually retype opaque values. If the response lacks a legal recovery contract,
fail closed with `CORTEX_PROTOCOL_FAILURE retryable=false`; never inspect
source, plugin cache, logs, database, session, environment, or hidden paths.
Raw JSON-RPC/protocol failures (for example `-32602`) that do not carry the
Cortex structured contract have no recovery authority: stop and fail closed.

## Exact authority pairs

`start_orchestration` starts a new task and returns the only coordinator
authority pair:

```text
task_ref + coordinator_ref
```

Keep that pair private to the coordinator. Cortex owns and issues both opaque
references; the model may only preserve and serialize the exact returned bytes.
It is a bearer capability: never
write it to a project file, Cortex result, worker briefing, event, diagnostic,
native tool argument, terminal output, or user message. Every task-scoped
coordinator call requires the exact pair:

- `continue_orchestration({task_ref, coordinator_ref, step, results})`
- `manage_orchestration({task_ref, coordinator_ref, intent, ...})`
- `manage_governance({task_ref, coordinator_ref, action, ...})`
- coordinator `read_worker_result({task_ref, coordinator_ref, step})`; the server derives the current wave and returns all canonical wave results plus continuation

`task_ref` alone never authorizes inspection, continuation, management,
governance, result reads, recovery, or a replacement. Never derive a missing
pair from the current thread, a session, process, environment, hook, project,
ledger, task list, native child identity, prior result, or user wording.

Each native worker receives a different exact pair in its returned
`spawn_agent` bootstrap:

```text
task_ref + assignment_ref
```

Before any Cortex tool call or project read/write, the child checks that both
fields are present in that server-issued bootstrap. If either is missing, it
makes zero Cortex/project calls (including no `worker_question`), performs no
session/environment/thread/path/database reconstruction, and returns only the
sanitized child final
`CORTEX_WORKER_BOOTSTRAP_MISSING missing_fields=[...] retryable=true`; it never
includes either capability value. The bracket content is the non-empty ordered
comma-separated subset of `task_ref,assignment_ref` that is missing. The
coordinator may repair that same native child exactly once with `followup_task`
by byte-copying the returned dispatch's exact server-built
`bootstrap_repair_message` byte-exactly; it must never reconstruct a message or pair. It
then waits on the same child again and must not spawn or substitute a second
child. A repaired child with both refs valid must not emit a gate-passed
acknowledgement: it immediately calls `read_dispatch_briefing` with the repaired
pair, consumes the complete briefing, and continues the original assignment
through `complete_attempt` to the exact `ATTEMPT_COMPLETED` final. Gate-passed
prose without that briefing call is a nonterminal protocol failure. After the
single repair, accept a terminal child response only as the exact second
bootstrap-missing marker, a durable `QUESTION_RECORDED` branch, or exact
`ATTEMPT_COMPLETED` after canonical progress. Bootstrap finalization is legal
only for the exact second bootstrap-missing marker. A briefing receipt proves
the worker pair was valid, so any later child tool/schema/protocol failure is
not bootstrap loss and must never call `finalize_bootstrap_failure`; follow
only the returned structured recovery on that same child. A second
missing/invalid bootstrap report, or loss of that server-built message by the
coordinator, is terminal. Call `manage_orchestration` once with
`intent="finalize_bootstrap_failure"`, the exact `task_ref`, exact
`coordinator_ref`, and the exact original
`payload={dispatch_ref, reason_code:"bootstrap_missing_identity"}`. Do not
create a replacement, event, receipt, worker result, or repair submission.

The exact child marker `CORTEX_ATTEMPT_FAILED retryable=false` is status text,
not failure authority. Only when it follows a structured
`recovery.terminal_failure.evidence="server_bound"`, call
`manage_orchestration` exactly once with the same `task_ref`,
`coordinator_ref`, `intent="finalize_worker_failure"`, and original structured
`payload={dispatch_ref, reason_code:"worker_nonretryable_terminal"}`. Never
copy child prose into the reason. The server verifies and consumes private
current task/attempt/dispatch/generation evidence before it blocks the task and
terminalizes that assignment/session without an `AttemptResult`, replacement,
or dispatch. Missing, stale, wrong-dispatch, and replayed evidence rejects the
call without mutation; result reads and continuation remain forbidden after a
verified transition.

Workers preserve that pair unchanged on every worker call:

- `read_dispatch_briefing({task_ref, assignment_ref, cursor?})`
- `record_attempt_event({task_ref, assignment_ref, event_type, payload, event_key?})`
- `worker_question({task_ref, assignment_ref, action, ...})`
- `complete_attempt({task_ref, assignment_ref, plan|outcome|repair...})`
- worker `read_worker_result({task_ref, assignment_ref, attempt_result_ref})`

For a resumed durable single question—whether its answer is scalar free text
or a selected stable option ID—the worker call is exactly
`worker_question({action:"poll", task_ref, assignment_ref, question_ref})`.
The coordinator's returned `resume={kind:"poll",question_ref}` is forwarded
to the same child through `followup_task`; `kind` is copied into the scalar
`action` field, never passed as an object, and the worker still includes both
of its original authorization strings. If validation reports a `/question_ref`
mismatch, correct only the advertised localized field/path on the same durable
question. Do not remove, recreate, or replace the question or its ref.

A worker receives neither `coordinator_ref` nor authority to call coordinator
tools. An `attempt_result_ref` authorizes only the one granted predecessor
read; it never replaces either required pair.

## Schema-first, same-attempt validation

Before every Cortex call, use the exact active `tools/list` schema. Never
invent fields from prose, old responses, host state, or field-presence
heuristics. The response-handling contract above is the only retry authority:
preserve valid fields, correct only returned paths, and never guess a ref,
value, field, or patch operation.

Worker responses are closed minimal unions. A briefing success contains only
content, encoding, `complete`, and an opaque `next_cursor` when more content
exists. Question successes contain only their exact ref and canonical
answer(s) when answered. A caller-recoverable response has top-level
`error={code,category,message,diagnostics}` and `recovery={kind,operation,retryable,state_mutated}`, with one
minimal `field_schema` per path. Never rely on, request, or reconstruct omitted
identity, receipt, telemetry, echoed input, full request schema, resume
context, or prose action field.

`complete_attempt` is the sole v11 worker submission surface. Submit exactly
one compact `plan` or `outcome` draft with the worker authority pair. If it
returns a repair capsule, retry the *same* `complete_attempt` with the same
pair, its exact `repair_capsule`, `base_payload_digest`, and only
diagnostic-scoped RFC6902 `patches`. Do not resend the rejected full draft,
invent a patch path, or invoke a separate repair operation. Treat
`repair_capsule` as an opaque server handle: never decode, reconstruct, or
manually transcribe it. Copy it directly from the structured response. Each
returned repair diagnostic is self-contained: use its `repair_pointer` as the
patch path and its `allowed_ops`, `code`, `message`, and `field_schema` to
choose the RFC6902 operation and valid value; never inspect Cortex source,
schemas, logs, or the ledger to discover a repair value. Once repair is locked
to that exact capsule, base digest, and returned path set until a valid patch
succeeds.
A full `plan`/`outcome` resubmit, an empty patch list, an out-of-scope path, or
an invalid patch op/value, or a malformed capsule copy is a retryable unchanged
repair response; use its top-level `recovery.kind=repair_patch_only` branch again. Only
a structurally valid capsule with a failed integrity check, tampered authority,
mismatched identity or base integrity, or an already-terminal attempt is
nonretryable.

An accepted completion has `ok=true` and `terminal=true`. It is terminal for
that worker: make no later `record_attempt_event` or worker
`read_worker_result` call. The worker final message must be exactly
`ATTEMPT_COMPLETED`; do not copy or hand off `attempt_result_ref`. The
coordinator reads canonical wave results with
`read_worker_result({task_ref, coordinator_ref, step})`. On
`retryable=false`, stop every task-scoped call. Return exactly
`CORTEX_ATTEMPT_FAILED retryable=false` only when the structured recovery also
contains `terminal_failure.evidence="server_bound"`; otherwise fail closed
without claiming that coordinator cleanup is authorized.

## Native V2 lifecycle

1. Call `start_orchestration` once for a new, explicitly activated task.
2. For every returned dispatch, invoke precisely the returned native
   `spawn_agent` call. Do not turn it into a visible-task route, a shell
   command, a non-native execution layer, or a different collaboration tool.
   `action.kind=invoke_dispatches` is the `spawn_all_exact_before_wait`
   contract: execute every exact returned native `spawn_agent` dispatch before
   any wait. Execute every exact returned native spawn_agent dispatch before any wait.
   It grants no wait permission and does not mean a worker exists.
3. Treat a dispatch as started only after native `spawn_agent` returns its
   child identity and the runtime reports the exact durable binding. Do not
   guess, prefix-match, or map identities across children.
4. Call native `wait` or `wait_agent` only for exact eligible live child IDs.
   A host wait-any call is allowed only when the runtime reports one or more
   eligible live children. Never wait for question-paused,
   completion-pending, stopped, unknown, or foreign children.
   `action.kind=wait_for_bound_workers` is the
   `wait_existing_returned_child_ids_only` contract: wait only on existing
   child IDs returned by successful `spawn_agent` calls. Wait only on existing child IDs returned by successful spawn_agent calls.
5. A timeout, generic error, malformed output, ambiguous response, partial
   multi-child response, or failure naming another child leaves the target
   live. Only an exact host proof that one exact bound child is unavailable can
   retire that child. The hook never decides this transition.
6. When a worker reports canonical completion, read it through the exact
   coordinator result form and call one exact `continue_orchestration` request
   with the server-derived step and result ref. Follow only the next returned
   dispatch, wait, task question, plan approval, or terminal outcome.

The orchestrator's chosen pipeline remains authoritative. Documentation and
close recommendations are advisory; it never forces a Planner wave solely
because of a failure. `task.plan_approval` defaults to `auto` for every complexity. Use
`required` only when the user explicitly requested post-plan approval;
governance, Planner, risk, and review recommendations never request it on the
user's behalf.

`followup_task` is permitted only to resume the same child after its durable
worker question is answered, or for the single bounded missing-bootstrap-pair
repair above. Do not use it to repair a stopped child or a failed submission.
Close a child only after its canonical terminal result or the host's exact
terminal lifecycle outcome.

`finalize_bootstrap_failure` may finalize only the current server-bound
`awaiting_spawn` assignment. Its exact retry is idempotent; afterwards do not
spawn, continue, or read a worker result for that dispatch.

## Questions, pauses, and final delivery

Workers ask questions only for a material task requirement, scope, acceptance,
or explicit external/destructive authorization. The coordinator routes the
durable answer, then resumes that exact paused worker with `followup_task`.
Internal validation, lifecycle, policy, worker, and telemetry conditions are
not user questions.

### Worker question union

For `worker_question(action:"ask")`, provide the exact worker pair plus a
top-level `question_type` and `decision_scope`. `question_type` is exactly one
of `single_select`, `multi_select`, or `text`; `decision_scope` is one public
enum value from the tool schema. Do not send `context`, `answer_mode`, or
`multiple`.

- `single_select` sends `options` and a one-item
  `recommended_option_ids` array.
- `multi_select` sends `options` and one or more
  `recommended_option_ids`.
- `text` sends only a concrete `recommended_answer`; it does not send
  `options` or recommended option IDs.

For `ask_batch`, every item uses this same explicit union and never a batch
`type` field. `poll` and `poll_batch` contain only their exact ref form and the
worker authority pair. Do not mix ask, poll, or batch fields. A durable question
is created only after `ok=true`; validation recovery never authorizes a second
question.

After a child returns exactly `QUESTION_RECORDED`, the coordinator calls
`manage_orchestration(intent="question", payload={question_ref})` with its
existing private coordinator pair. Its payload contains only the exact
`question_ref`; Cortex derives the canonical card, options, and localization.
Never construct or submit localized display fields, recreate the question, or
change the durable ref or worker assignment.

On `awaiting_user`, render only the returned user card and end the turn. On
the next ordinary user message, submit the answer with that same
`payload.question_ref`. Only `question_answered` authorizes one
`followup_task` to the exact paused child; its next Cortex call is the returned
`worker_question(action="poll", question_ref=...)`. Do not spawn a
replacement, remove and recreate the question on a `question_ref` mismatch,
or call a project tool before the poll. Preserve the exact-once durable
question; scalar answers and stable-option selections resume the same child
through that exact poll form.

Visible output is limited to a worker result, a task-relevant question or
explicit safety/authorization boundary. A Cortex-owned governance, briefing,
receipt, ledger, gate, or other internal evidence gap belongs in findings and
AttemptEvents; return `orchestrator_advice` to the coordinator for corrective
ownership. Do not ask the user, create a durable question, or remain idle for
such an internal gap.

The coordinator may pause ordinary delivery only for a durable worker question
or an explicit returned plan-approval state. It may deliver a final user answer
only after Cortex returns the exact terminal outcome and its canonical result
has been read. A native event, a hook marker, a child final message, an empty
wait, or a planned dispatch is never completion evidence.

## Bounded compaction handoff

At compaction/reset, preserve the existing coordinator `task_ref` and
`coordinator_ref` together in the bounded private handoff, plus the current
goal, exact pending native IDs, canonical result refs, and next server-derived
step. Do not put a raw capability in durable state, logs, artifacts, worker
messages, or user output.

If both values survive, use them only with an exact server-returned recovery
operation when one is required, then follow its returned state. If either value
is absent, fail closed: do not inspect, recover, query a ledger, bind a host
session, infer a task, or produce a replacement capability. Obtain fresh user
direction and begin a new explicitly activated task if appropriate.

## Hooks are telemetry only

The bundled hooks observe only exact native `spawn_agent`, `wait`,
`wait_agent`, start, and stop boundaries. Their output contains no identity and
cannot authorize a worker, bind a child, write lifecycle state, reconstruct a
capability, inject a briefing/ledger path, retire a worker, or dispatch work.
A missing-capability marker is neutral fail-closed guidance, never a recovery
route.
