---
name: cortex-control
description: Use this skill when coordinating a non-trivial task across Codex agents and durable gate, delegation, lock, or handoff state is useful. It uses the local cortex MCP server; choose each subagent's model and reasoning effort dynamically at dispatch time.
---

# Cortex Control

Orchestration is inactive by default. Selecting a non-help, non-`normal` route
from the native `cortex:orchestrator` skill authorizes the main/root Codex agent to call
`activate_orchestration` with the canonical `/cortex` token, its principal, and
its thread id. Tell Desktop users to select Cortex Orchestrator in the Skills picker or use
`$cortex:orchestrator`; tell CLI users to lead with `$cortex:orchestrator` or `/skills` and
select Cortex Orchestrator. The `normal` skill argument authorizes deactivation. Literal
`/cortex` and `/normal` are textual shorthand, not registered native slash
commands, and a host may reserve them. Ordinary requests never initialize a
ledger. Without activation, all mutating control-plane calls must fail;
`get_activation_status` remains available for status inspection.
When the bound project ledger contains exactly one valid activation,
`get_activation_status` may omit principal/thread identity and returns
`identity_inferred: true`; multiple activations return a non-throwing
`next_action` asking for identity.

Use this control plane for C1/C2/C3 work that spans agents, phases, or a
resumable handoff. It is opt-in: ordinary tasks do not need a ledger. While
active, the main agent owns goals, decisions, routing, gates, user
communication, and integration; project inspection, search, execution,
testing, and editing belong to hidden/internal workers.

1. **Fail closed before project work.** `activate_orchestration` must carry the absolute `project_root`; it binds an immutable workspace for the MCP-process lifetime. Later calls may repeat the same root or omit it and let the server restore the bound value; an attempted root switch is always rejected. Do not read, search, edit, execute, test, or dispatch against the project until this sequence succeeds: `activate_orchestration` → `classify_task` → `init_task` → `get_task_status`. The returned task must name that exact root and its ledger must be `${project_root}/.codex/cortex`. If the MCP server is unavailable, the initial root is absent, the directory is unwritable, the returned root differs, or a `/tmp`/other fallback appears, stop and report the blocker. Do not use an ordinary-subagent or unledgered fallback.
2. Call `classify_task` once with complexity, concrete requirements, the full gate proposal selected by the main orchestrator in `pipeline`, and ordered independent waves in `parallel_groups`, then call `init_task` once with its `classification_id`, objective, and the current `principal`/`thread_id`. Gates in one wave may run concurrently only when dependencies, paths, and resources do not conflict; the next wave waits for all gates in the current wave. The orchestrator owns all optional gates; Cortex appends only the mandatory `documentation` and `close` gates. The receipt is the authoritative complexity, requirements, initial pipeline, and waves contract. Duplicate `complexity`, `requirements`, `pipeline`, or `parallel_groups` fields sent to `init_task` are ignored; initialization always consumes the receipt values and reports corrections when supplied values differ. This creates a private durable ledger task and binds mutations to that principal.
   Gate IDs are canonical and lowercase: `plan`, `discover`, `architecture`, `database_architecture`, `implementation`, `qa`, `security`, `performance`, `accessibility`, `ux`, `review`, `documentation`, and `close`. The MCP boundary normalizes only bounded compatibility aliases such as `planning` → `plan`, `discovery` → `discover`, and `verification` → `qa`; unknown IDs remain hard validation errors.
   If the same principal initializes an existing task id again, Cortex resumes and rebinds that immutable task instead of failing on regenerated objective wording; it returns `objective_correction` and preserves the original task definition and current ledger state.
   Human-readable `task_kind` values are accepted at delegation and canonically stored as lowercase underscore identifiers (for example, `Code Review` becomes `code_review`).
   `record_delegation` also treats client revision, status receipt, and requested gate as recoverable hints: it serializes against the actual current ledger state, reports any `revision_correction`, `receipt_correction`, or `gate_correction`, and does not emit a tool error for stale values. Always pass both the coordinator `principal` and bound `thread_id`; they are distinct fields. If a native call omits `principal`, only the exact bound thread can recover it. A premature passed gate returns `recorded: false` with `next_action`. `record_evidence` infers a missing attempt and report receipt only when both choices are unique; otherwise it returns a non-mutating `next_action`. Follow that action and retry. Never treat a `recorded: false` or `recoverable: true` response as permission to dispatch another worker: follow `next_action`, reuse its candidate attempt, and stop after a repeated identical recovery reason. Documentation evidence kinds are canonicalized to `documentation`, and a legacy documentation receipt is repaired at gate transition without spawning another technical writer. Authorization failures, root switches, ambiguous choices, and security violations still fail closed.
   `execute_verification_command` follows the same recovery contract: it corrects a stale revision, binds the current gate, and returns a structured `next_action` when an attempt or report receipt is not yet available. Documentation evidence is normalized to the `technical_writer` decision when a coordinator supplies a generic verification kind. Read-only task kinds propagate `read_only: true` to every profile, including documentation and verification workers.
   Missing delegation gate, profile, task kind, and risk are also inferred from the current ledger gate and returned as `agent_correction`, `task_kind_correction`, and `risk_correction`. Canonical defaults include `plan → planner`, `discover → explorer`, `qa → qa_engineer`, `security → security_auditor`, and the corresponding specialist for other standard gates.
2. Before dispatching a hidden subagent, inspect the native host `spawn_agent` tool and pass its exact models as `available_models` to `record_delegation`. If a Luna policy route is unavailable there but Terra is available, Cortex selects Terra and records `fallback_reason=host_model_unavailable` plus `fallback_from_model=gpt-5.6-luna`; do not call it Luna. Other unavailable routes fail closed. A visible Luna task is different: use `dispatch_mode: visible_thread` only after the user explicitly asks to create that task. Inspect native `create_thread`, pass its exact catalog as `available_thread_models`, and let Cortex return its `create_thread` request. It preserves the routing-selected effort—do not force `max` for a narrow task. Call `list_projects` first for repository work and default to a worktree when it is a Git repository. The attempt remains `awaiting_host_spawn` until the host returns a real child/thread id. Confirm with `confirm_host_spawn`, providing `host_tool: create_thread` and its `threadId` as `host_agent_id` for a visible task, or `host_tool: spawn_agent` and the child id for a hidden worker, plus actual model and effort. The ledger checks both tool and model; an omitted model is recoverable and a mismatch terminalizes the attempt. Visible tasks are user-owned sidebar items, not automatic fallbacks and not hidden workers; monitor them through `wait_threads`, inspect with `read_thread`, and send context with `send_message_to_thread`. For either route, an adapter label difference is a `task_name_correction`, a racing report remains pending confirmation, and a native creation failure must be finalized as failed or cancelled with its reason. The `spawn_request.message` prompt contains the exact profile instructions, so an adapter such as `multi_agent_v1` exposes no title field without changing the canonical profile. Worker work, reports, questions, and handoffs remain English-only and the coordinator alone translates user-facing results.
   The coordinator must actively monitor every dispatched attempt, not only its final text: after dispatch, keep the native host wait open with `timeout_ms >= 10000` and poll `list_worker_questions` during each wait interval, because a hidden worker's durable question may arrive before its host turn completes. After each completion, timeout, or newly listed question, call `get_task_status` and inspect that attempt's host binding, status, report ids, evidence ids, model, and reasoning effort. `list_task_reports`, `list_worker_questions`, and `reconcile_report_bus` are the durable sources of truth. The spawn briefing gives the worker the exact canonical `attempt_id` and a lowercase stable `submission_id`; `record_report` can infer omitted identifiers only when the worker identity maps to one active attempt and otherwise returns a recoverable candidate list. If a worker reports an MCP error, preserve the exact tool name and error text in its report; if it cannot publish a report, immediately finalize that attempt as `failed`, `blocked`, or `cancelled` with the exact error as the reason. Never silently continue, retry with a different attempt id, or treat an unobserved host result as success.
3. Keep read-only discovery/review work parallel only when their paths and decisions do not conflict. Serialize writers or use isolated worktrees. Multiple independent agents may be recorded within one gate or across gates in the current `parallel_groups` wave with `parallel=true`; each gate still needs its own evidence and outcome, and terminal non-success attempts need an explicit reason. Use `claim_resource`/`release_resource` for exclusive branches, ports, processes, databases, or other shared resources; `acquire_lock` remains an advisory compatibility API.
   Use `prepare_delegation` for the common status-receipt + delegation path. For independent read-only or otherwise non-conflicting workers in the current wave, use typed `prepare_delegations` with every spec marked `parallel=true`; it returns independent spawn requests across the listed active gates and never batches arbitrary MCP tools. A mid-batch validation or routing failure rolls back the batch and returns the exact error as a recoverable result. The legacy `get_task_status` → `record_delegation` sequence remains supported.
4. Worker questions use the durable pull bus plus the native main-chat UI; never assume a host push channel from a hidden worker. A worker should call `cortex.question` with its own `attempt_id`, a stable lowercase `submission_id`, the ambiguity, and concrete `options` when useful. Cortex persists the question and returns `pending_user_input` without opening a worker-local UI. The main agent must poll `list_worker_questions`, then call `cortex.question` again with the returned `question_id` and no `attempt_id`; only that coordinator call opens the native `elicitation/create` form in the main chat. The form always ends with a free-form `custom_response` field. Single-select options use a radio-style control; `multiple=true` uses checkboxes and returns `selections`. The coordinator must not invent a choice: it forwards the user's accepted content with `answer_worker_question` (or lets `cortex.question` persist it) using a stable answer submission id and explicit `resume_context`. The worker resumes by polling `get_worker_question_updates` with its own attempt and last `after_sequence`. Preserve structured answer content, including host-provided attachment/image metadata, instead of coercing it to plain text. Task-principal authorization and attempt scoping are mandatory; workers must not read another attempt's updates.
   Multiple open questions are supported: the bus has a global monotonic sequence and preserves `attempt_id`, profile, and question id for every record. Present open questions one at a time in `published_sequence` order (or explicitly group them in the main chat), answer each independently, and keep polling until `open_count` is zero. Never merge answers from different attempts.
5. Every worker should call `record_report` with exactly `summary`, `findings`, `questions`, `changed_files`, `tests`, `evidence`, `uncertainty`, and `next_action`. Use a stable `submission_id` for idempotent retry. For C2/C3, consume that attempt's one-use report receipt with `record_evidence` or `execute_verification_command`. The worker receives the receipt when publishing; the coordinator retrieves it by calling `get_delegation_reports` for the producer attempt and reading `reports[].receipt.receipt_id`. A downstream context grant exposes report bodies but does not transfer their receipts. If an interrupted coordinator has only the exact owned `report_id`, it may supply that as the `report_receipt` hint and Cortex will resolve it only when task, gate, attempt, and active receipt all match. Never guess receipt formats. Command evidence must include `exit_code`; use `execute_verification_command` whenever command proof matters. A C2/C3 close requires at least one successful server-observed command (exit 0), not a self-attested command. Then call `record_gate_outcome`. A passed attempt must have linked evidence; terminal non-success attempts (`failed`, `blocked`, `cancelled`, or `superseded`) may lack a report when the coordinator records an explicit reason. Mandatory documentation/close skip requests return a structured recoverable response directing the coordinator to delegate the gate. A skipped gate advances without a pass claim. `blocked` preserves the gate and marks the task blocked; C2/C3 blocking also requires a handoff at the current gate, then use `resume_task` after the blocker is resolved.
   Use `complete_attempt` when the native adapter can provide host identity and, optionally, a strict `cortex/report/v1` payload. It combines confirmation, idempotent report publication, and terminalization; the legacy three-call path remains the fallback. Use `commit_gate` to combine server-observed verification (or a documentation decision) with the gate outcome. A failed verification records failure evidence but never passes the gate. Use `close_audit` to combine close-time report listing and reconciliation.
   `record_report` accepts omitted `attempt_id`/`submission_id` for native workers as a recovery path: the server derives the attempt from the worker alias only when unambiguous and derives a deterministic lowercase submission id from the report digest. Explicit malformed identifiers still fail closed. This prevents a worker's profile label, display name, or an empty field from becoming a ledger identifier.
5. If repository evidence changes scope, call `reassess_pipeline` with typed `intent` (`add_specialist`, `resequence`, `rework_gate`, or `stop`) and new signals. For a pipeline decision, pass the orchestrator's complete replacement in `pipeline`; Cortex validates it, appends only `documentation` and `close`, and applies additions, removals, and reordering when `decision=updated` and `apply: true`. Removing a completed gate requires `allow_rework: true`; without it the existing evidence is protected. Review the proposal before applying it. Replanning is bounded by the task retry budget; after exhaustion, stop and hand off. `update_pipeline` supports atomic `add`, `remove`, `move`, `replace`, and explicit `rework` operations. Rework resets completion without deleting the gate or its prior attempts. The original task definition remains immutable.
6. Before compaction, escalation, or parent handoff, call `create_handoff` with completed work, files, decisions, risks, and the exact next action. It reconciles the initialization manifest: list every changed project-relative path, including additions, deletions, modifications, and renames. If the list is incomplete, Cortex returns `recorded: false`, `unaccounted_paths`, and `next_action: retry_create_handoff_with_complete_files`; add those paths and retry without treating the response as a task failure. Use `reconcile_project_files` earlier if a receipt is needed to identify omissions. C2/C3 tasks also require a final close-gate handoff with a complete manifest receipt; any later project change requires a new handoff.
7. Record only non-sensitive task metrics when the repository explicitly needs them: scope, checks, verdict, material risk, and durable-artifact location. Do not add token counts, model identifiers, raw prompts, or private telemetry. The model and reasoning effort required in a delegation record remain part of that delegation contract, not general metrics. Task state and gate receipts are authoritative.
   Internal worker communication is English-only: worker prompts, commentary, progress updates, tool arguments, reports, findings, question records, handoffs, audit events, and final answers must be written in English. This also covers a visible Luna task: its sidebar visibility does not make it a user-language channel. The coordinator alone owns user-facing language and must use the language of the task (or an explicit `user_language`) when presenting questions, blockers, decisions, and summaries. It may pass `localized_question`, `localized_options`, and related display fields to `cortex.question`; those fields never replace the durable English worker record.
   For repository/code search, workers must first call `mcp__codebase_memory__list_projects`, match the exact absolute `project_root` to an indexed project, and pass the returned project identifier/path to later codebase-memory tools; never guess the identifier. Only then prefer `search_graph` for definitions and relationships, `search_code` for textual matches, `trace_path` for callers/dependencies/data flow, and `get_code_snippet` after locating an exact symbol. Do not begin with `grep`, `rg`, globbing, or ad-hoc filesystem scans when it is available. If `list_projects` fails, it is unavailable, or no indexed project matches, do not call other codebase-memory tools; record that limitation and use another search method only as a documented fallback; never claim a lookup that did not run.
8. Use lanes only when execution needs a durable workstream: `create_lane` → `claim_lane` → `bind_task_lane` → optional `materialize_lane` → task gates → `reconcile_lane` → resource release → `release_lane`/`retire_lane`. Claims collide globally across tasks and lanes; use expiration and release them. Materialization requires a live lease and explicit `confirm=true`, uses only declared absolute paths, never force-removes worktrees, and refuses dirty retirement. Expired leases require explicit `reclaim=true`; attached worktrees are not treated as managed and are never removed.

Continuation note: a resumed root coordinator may be spelled `/root` by the
host even when the durable task owner is `root`; Cortex normalizes that exact
alias. Other principal changes remain authorization failures.

## Main-agent close protocol

After any dispatch, the main agent must complete this protocol in order. Host
completion and ledger completion are separate facts: a host-side terminal
worker does not by itself terminalize its ledger attempt.

1. Wait for every dispatched host worker in every gate in the current
   executable wave to reach a
   terminal host state. Keep workers hidden and route their questions,
   blockers, reports, and handoffs only through the main chat; workers never
   send a user-facing final message.
   Native Codex collaboration waits must use `timeout_ms >= 10000`; never send
   a smaller timeout because the host rejects it before Cortex can recover it.
2. Call `get_task_status`, then `reconcile_report_bus`. Reconcile the complete
   set of host-terminal workers against the gate's ledger attempts. After every
   host completion or timeout, call `finalize_attempt` for the corresponding
   running attempt. Use exactly one of `passed`, `failed`, `blocked`,
   `cancelled`, or `superseded`; non-`passed` statuses require a non-empty
   reason. If the reason is omitted, Cortex returns a recoverable
   `next_action=retry_finalize_attempt_with_reason` response instead of an MCP
   exception. An invalidated running attempt may only be finalized as
   `superseded`, with a non-empty reason; already-terminal invalidated attempts
   remain idempotent. A gate with only terminal non-success attempts may pass
   without report or evidence when acceptance permits partial failure. Do not
   advance while a dispatched worker is still active, an
   expected attempt is absent, or an unrelated non-terminal attempt remains
   unexplained.
3. Call `list_task_reports` and require a strict `cortex/report/v1` report for
   every attempt that will be passed. A host-terminal attempt without a report
   must be finalized with a non-success status and reason. Grant reports needed
   by a downstream attempt with `grant_report_context`; report bodies may be
   retrieved by that attempt with `get_delegation_reports` only after the
   explicit grant.
4. Consume each passed attempt's one-use `report_receipt` with that attempt's
   `record_evidence` or `execute_verification_command` call. Confirm that the
   resulting evidence links the attempt, report, receipt, and current gate.
   A host terminal result, report text, or self-attested command is not
   evidence by itself. Terminal non-success attempts remain visible in status
   and may be carried through a successful gate when acceptance allows it.
5. Record the current gate outcome for each active gate only after that
   gate's host workers are terminal,
   every ledger attempt is terminalized, and every passed attempt has its
   report-backed evidence. Then call `get_task_status` again and confirm the
   gate outcome preserved terminal non-success statuses, terminalized any
   evidence-backed running attempt for backward compatibility, and advanced
   the expected revision/current gate.
6. Reassess when evidence changed scope, including whether waves must be
   split or merged, apply any accepted pipeline update, and run or explicitly
   reconcile every remaining gate. Repeat steps 1–5 for every gate in the
   wave; advance only after the whole wave is resolved, and never jump
   directly from a host completion to task completion.
7. At `close`, confirm that every non-invalidated attempt in the task is
   terminal, that every passed attempt has a report plus linked evidence,
   that terminal non-success attempts have recorded reasons, that required
   gate and reassessment receipts exist, and that a successful server-observed
   command is recorded. Reconcile all changed project paths, create the final
   close-gate handoff with a complete manifest, record the close gate outcome,
   and call `get_task_status` once more. Any project change after the handoff
   requires a new complete handoff before closing.
8. Only after that final status reconciliation may the main agent send the
   user-facing final message. It must state `PASSED`, `FAILED`, or `BLOCKED`
   and list changed files, exact tests/results, and limitations. If one or
   more attempts ended `failed`, `blocked`, `cancelled`, or `superseded`,
   explicitly report that partial failure even when acceptance criteria allow
   the task and gate to close. A failed or blocked path still requires the
   applicable gate outcome and handoff before the main chat reports it.

## Dispatch policy

Profiles deliberately do not pin `model` or `model_reasoning_effort`. The 21
supported names come from `plugins/cortex/profiles.json`; `task_formatter` is
not supported. Choose requested capability per call:

- multi-agent v2 is required for explicit per-worker model selection;
- Luna handles reading, discovery/data gathering, investigation, diagnosis,
  research, code review, CRUD-level edits, and small fixes when the
  orchestrator declares that intent in `task_kind`, independently for every
  delegation and at any risk;
- a read-only profile alone does not imply Luna; non-analysis work uses
  `gpt-5.6-terra`, including architecture, migration, debugging, and
  implementation work;
- security task kind, the security gate, and the `security_auditor` profile
  use `gpt-5.6-sol` regardless of risk, normalizing contradictory task kinds
  to security; and
- non-security Sol is an exception only when `sol_escalation` is either an
  auditable extreme classification (a supported criterion plus `audit_ref`) or
  a `terra_failure` linked to a failed Terra attempt in the current ledger.

Task complexity controls ledger gates, evidence, documentation, and handoff
requirements; it does not choose the worker model. `finalize_attempt(passed)`
records host completion and may precede evidence linkage. A gate still cannot
pass until its required report-backed evidence is recorded.

`escalation_reason` is retained as context only; free-form text never
authorizes Sol. The supported auditable criteria are
`irreversible_multi_system_recovery`, `safety_critical_incident_response`, and
`novel_cross_system_failure_without_bounded_rollback`.

Requested reasoning effort `none` normalizes to `low`. For Luna
analysis/lightweight work, the default and minimum are `medium` at
low/moderate risk, `high` at high risk, and `xhigh` at critical risk; an
explicitly higher requested effort is preserved. The orchestrator must declare
analysis/discovery intent instead of relying on a read-only profile.
Canonical examples include `discover`, `data_gathering`,
`runtime_investigation`, `diagnosis`, `research`, and `code_review`; generic
`implementation` should be reserved for work that actually changes code.

Use the existing role profiles (`explorer`, `planner`, `architect`, `backend_dev`, `qa_engineer`, `code_reviewer`, `security_auditor`, `technical_writer`, and specialists) for role instructions; the main agent owns scope, integration, and final verification.

## Durable artifacts

`task.json` contains the immutable original objective, scope, acceptance criteria, budget, verification, initial pipeline, and manifest policy. `current.json` holds mutable status, gate, revision, attempts, evidence, locks, documentation/reassessment/manifest receipts, and justified pipeline changes. Production workflows always use `${project_root}/.codex/cortex`; `CORTEX_ROOT` is rejected by the server, and `/tmp` is never an acceptable ledger. `CORTEX_PROJECT_ROOT` is not a replacement for the mandatory per-call absolute `project_root`. New task directories are numbered as `tasks/0001-task-id/`; the stable `task_id` remains the API identifier. Delegation packages, evidence receipts, reports, metrics, lifecycle events, and handoffs are stored next to the task. Delegation correlation is `ledger_attempt_only`: `principal` and `thread_id` authorize ledger access but do not prove main-agent identity or a host-side spawn. Hooks are telemetry-only. Do not place source code or secrets in these coordination files.

Report JSON under `reports/records/` is authoritative. The task index contains
metadata; delegation indexes contain owned and explicitly granted report ids;
receipts bind reports to evidence; and escaped Markdown is generated. Use
`grant_report_context` before reading bodies with `get_delegation_reports`.
`reconcile_report_bus` validates records and repairs indexes, missing receipts,
and Markdown. Each file is atomically replaced, but publication is not
multi-file crash-atomic.

### Dynamic pipeline operations

Use gate ids such as `security`, `database_architecture`, `performance`, `accessibility`, `ux`, or a project-specific specialist gate. Examples:

```json
{"operations":[{"op":"add","gate":"security","before":"review"}],"reason":"OAuth token flow discovered"}
{"operations":[{"op":"move","gate":"qa","before":"implementation"}],"reason":"test-first migration required"}
{"operations":[{"op":"replace","gate":"implementation","with":["backend_implementation","frontend_implementation"]}],"reason":"ownership split after discovery"}
```

`reassess_pipeline` accepts the orchestrator's full replacement when `pipeline`
is present. It proposes and, after the parent Codex explicitly applies it,
records additions, removals, and reordering without silently discarding
completed evidence. Every applied change is revision-guarded and written to
`current.json`, `journal.md`, `metrics.json`, and `adaptive_events`.
