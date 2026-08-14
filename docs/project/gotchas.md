# Gotchas

## Public v3 coordinator contract

- Normal flow is `start_orchestration` once, then
  `continue_orchestration` once per completed relative `step`.
  `manage_orchestration` is only for inspect/resume/deactivate and rare
  lane/resource/durable-question work. The legacy `orchestrate` facade and v7
  primitives below are private compatibility internals. Together with
  worker-only `record_report` and coordinator-only `read_worker_report`, the
  public surface is exactly five tools.
- Every public call requires the exact absolute `project_root`. Start needs
  only `task.objective`; complexity defaults to C2. Compact wave overrides use
  `waves[].workers[]`, with only `phase` required.
- Continue carries the prior response's relative `step`. A single result omits
  `worker`; a parallel wave must return every unique integer worker slot once.
  Stale, duplicate, missing, foreign, or changed retries fail before task-state
  writes.
- Successful results carry a compact `report_ref`. Workers persist all eight
  `cortex/report/v1` sections through the scoped public `record_report` tool,
  then return only `REPORT_RECORDED report_ref=<value>` plus at most a
  two-sentence summary. The coordinator reads the full report through
  `read_worker_report`. Non-success results omit report refs and require a
  normalized status plus reason. Workers never call lifecycle, pipeline,
  gate, delegation, or management operations and never paste report JSON into
  the native parent result. If `record_report` fails, the native final contains
  only the exact report-tool error. If persistence succeeds
  but the native acknowledgement is interrupted, inspect with
  `manage_orchestration` and recover the ref from `available_reports`.
- Embedded predecessor handoffs are mandatory input. Read and reconcile every
  supplied report before project work, then include the generated
  `Predecessor review:` entry naming every supplied report ref in report
  evidence; public `record_report` rejects an incomplete acknowledgement.
  Omit `depends_on` to receive all verified predecessors, provide exact earlier
  phases to narrow the set, or use `[]` only for intentional independence.
  Context count/size overflow fails closed instead of dropping older reports.
- Available `docs/project/index.md` and `docs/features/index.md` files are
  injected into every worker briefing. The planner names task-relevant linked
  pages for the coordinator to attach through `context_files`; downstream
  workers still re-check both indexes. Reports must include `Knowledge
  reviewed:` evidence naming every available index and additional page used,
  or public `record_report` rejects them. Treat documentation as navigation,
  not authority, and verify consequential claims in current source, tests,
  schemas, migrations, or executable configuration.
- Explicit `context_files` are not arbitrary host paths. Cortex rejects
  absolute paths, traversal, missing entries, symlinks, and anything that is
  not an existing project-relative regular file.
- Idempotency is server-owned. Callers do not send submission, task, wave,
  attempt, coordinator identity, or host metadata. Preserve the returned opaque
  `task_ref` on every later lifecycle and report-read call. Exact duplicate
  active starts replay; changed task/wave contracts create distinct concurrent
  tasks below the same project root. Omitting the ref while several tasks are
  selectable returns `needs_selection` with opaque candidates instead of a
  guessed active task.
- Human-readable language names normalize before ledger creation. Repeating a
  semantically unchanged `future_waves` assessment is valid and keeps the next
  relative step monotonic; it must not fail after committing the current gate.
- Common phase labels are bounded aliases, not new waves: `implement` maps to
  `implementation`, while `build_verification` maps to final `close`. Cortex
  rejects the same canonical phase repeated across later waves, so use the
  returned pipeline snapshot instead of relabeling completed work and retrying.
- Dispatch arguments contain only native parameters. Expected model/tool/
  effort is routing metadata, not actual host attestation. Do not add a native
  model when configured-default Luna intentionally omits it.
- Modern Codex clients advertise the `openai/form` MCP extension under
  initialize capabilities; the legacy boolean is still accepted. A durable UI
  question uses `manage_orchestration(intent="question")` and must return a
  recoverable unsupported result rather than guessing when the host cannot
  render elicitation.
- Fixture Luna-high evaluation covers sequential, compact parallel, and
  blocked/resume flows. A live `SKIP` means missing release evidence, not pass.
- The compact worker `profile` value must be one of the 21 canonical
  `profiles.json` names and must support the requested phase. Legacy aliases
  are runtime compatibility only; they are not part of the advertised enum.
  An unsupported phase/profile pair is rejected before ledger writes.
- Omitted implementation profiles are selected conservatively from bounded
  explicit signals in objective, requirements, acceptance criteria, scope,
  allowed paths, and verification. Rule order is `fullstack_dev`,
  `mobile_dev`, `devops_engineer`, `data_engineer`, `debugger`, `refactorer`,
  `frontend_dev`, then `backend_dev`; `general` is only the fallback. Do not
  treat this initial route as repository evidence: planner/explorer findings
  are advisory. The coordinator alone decides whether they justify replacing
  not-yet-started `future_waves`, and must include a concise reason.
- The coordinator builds or consciously accepts the initial pipeline, follows
  the returned snapshot by default, and owns every later pipeline decision.
  Planner and explorer reports can supply evidence but cannot command a
  replacement.
- Dispatch routing metadata is not part of the native host call. Read `phase`,
  `profile`, `capability`, `sandbox`, and `selection_reason` from the dispatch,
  then pass only `call` and its unchanged `arguments` to the native tool.
- Once Cortex is active, the main/root agent is coordination-only. It must not
  inspect, search, read, edit, patch, build, test, or run the target project,
  even when a worker is delayed, fails, or is unavailable. Dispatch only the
  workers returned by Cortex, remain idle while they run, and use recovery,
  rework, or a blocker instead of taking over their project work. `SessionStart`
  and every public v3 `next_action` repeat this rule so compaction or a resumed
  turn does not weaken it.
- Codebase Memory is worker-only and conditional. When its tools are present,
  call `list_projects` first and use only the project whose root exactly matches
  the task root; prefer graph, architecture, and trace operations, then confirm
  consequential facts in source/tests. `planner`, `explorer`, `architect`, and
  `database_architect` may refresh one missing or stale index; other profiles
  fall back to repository-native tools after one failed attempt. Never guess a
  project id or loop. An indexed repository never authorizes the root
  coordinator to inspect the project.

## Private v2/v7 compatibility internals

The remaining notes document invariants retained inside the v7 ledger and
private adapter. They are not valid public v3 request envelopes.

- Command evidence must include an explicit `exit_code`; a textual claim that
  a command was green is not sufficient. The final C2/C3 `advance` privately
  runs the allowlisted close verification and requires its server-observed
  exit-0 receipt; coordinators must not call the private verification primitive.
- Bearer tokens, URI credentials, quoted secrets, and secret-like environment
  assignments are redacted before ledger persistence. Do not place real secrets
  in task prompts or reports.
- `orchestrate(operation="resource", payload.command="claim")` is the explicit
  exclusive-resource API. Use an expiry for ports, processes, databases,
  branches, and other resources that may survive a crashed agent.
- The v2 coordinator workflow is `orchestrate(start)` followed by one
  `orchestrate(advance)` call for each completed wave. Historical `cortex/v7`
  tasks remain inspectable and resumable through recovery operations, but v7
  primitive names are private implementation details and are not a public
  coordinator workflow.
- C2/C3 gates cannot silently skip or remove `documentation` or `close` from
  the pipeline. There is no `learn` gate. The documentation gate must be
  delegated to `technical_writer` and record `updated` or justified
  `not_applicable`; close also requires at least one reassessment receipt.
  Other skipped C2/C3 gates require an explicit reason.
- Use `advance` to submit a reviewed future-wave replacement. A no-op remains
  unchanged; never claim a C2/C3 documentation or reassessment decision was
  applied without the corresponding durable operation. Replacing completed
  work requires `allow_rework=true`.
- `start` creates the authoritative classification and initializes the task;
  callers must not synthesize lifecycle receipts. Classification provenance is
  ledger state, not a worker report or task-owned mutable input.
- `principal` and `thread_id` are separate identity fields. Every coordinator
  call should send both. If a native call omits `principal`, Cortex may recover
  it only from the exact task-bound `thread_id`; an unrelated thread is
  rejected. The host's `/root` spelling is normalized to the durable `root`
  coordinator principal when resuming a root task; other principal changes
  remain authorization failures.
- Keep the coordinator identity pair stable across a task: use the exact
  bound `principal`/`thread_id` from activation or status, never a worker
  profile, native child id, or guessed `/root` value. If the resumed host turn
  does not know the bound thread, omit `thread_id` and let Cortex restore it.
  A worker's confirmed host id is accepted only for its own unambiguous report;
  it does not authorize coordinator mutations.
- MCP boundary failures are logged to the per-user system path
  `~/.codex/logs/cortex-tool-errors.jsonl` as redacted JSONL. The record keeps
  the chat/thread session id, JSON-RPC request id, and any task/attempt or
  other call ids, but never stores secret-like input values verbatim.
- Expected public v3 validation and recovery failures return structured
  `ok: false` results with bounded `diagnostics` and a corrective `next_action`.
  They are caller-correctable protocol outcomes and are not written to
  `~/.codex/logs/cortex-tool-errors.jsonl`; only raised MCP-boundary exceptions
  enter that private redacted log. Public v3 validation occurs before lifecycle
  writes where possible. Correct every diagnostic and retry according to the
  returned action.
- Preflight aggregates independent request mistakes into one `ok: false`
  response. Each diagnostic has `path`, `message`, and `expected`; repair every
  listed path before retrying. Do not treat the first diagnostic as the only
  error or submit a sequence of one-field fixes.
- The exact `start` envelope is `{operation:"start", project_root, principal,
  thread_id, submission_id, host_capabilities, task, waves}`. `task` requires
  `{task_id, objective, complexity}` where complexity is `C1`, `C2`, or `C3`;
  it additionally accepts `requirements`, `acceptance_criteria`, `scope`,
  `allowed_paths`, `verification`, `budget`, `pause_conditions`,
  `user_language`, and non-negative `replan_limit`. `host_capabilities`
  requires a non-empty `spawn_agent_models` array and additionally accepts
  `available_models`, `spawn_agent_default_model`, `create_thread_models`, and
  `available_thread_models`. `task` and `host_capabilities` reject every other
  nested key.
- Each start wave is exactly `{wave_id, delegations}`; `delegations` is a
  non-empty array (at most 32) whose entries require `gate`. A delegation may
  also contain `agent`, `task_kind`, `risk`, `requested_model`,
  `configured_default_model`, `available_models`, `available_thread_models`,
  `dispatch_mode`, `thread_environment`, `requested_reasoning_effort`,
  `user_requested_model`, `retry`, `parallel`, `objective`,
  `ownership`, `context_files`, `context_report_ids`, `context_gates`,
  `allowed_paths`,
  `acceptance_criteria`, and `verification`. Do not use the retired
  `{id, gates: [{id, owner}]}` form: use `wave_id`, `delegations`, `gate`,
  `agent`, and `ownership`; waves and delegations reject every other nested
  key.
- The exact `advance` envelope is `{operation:"advance", project_root,
  principal, submission_id, task_id, wave_id, completions}` with optional
  `thread_id`, `gate_outcomes`, `future_waves`, `allow_rework`, and `reason`.
  `completions` is non-empty and has one terminal object per active attempt.
  Every object requires `attempt_id`, `host_tool` (`spawn_agent` or
  `create_thread`), `host_agent_id`, `host_task_name`, `host_model`,
  `host_reasoning_effort`, and terminal `status`; it may include `reason` and,
  when passed, a `report` containing exactly `summary`, `findings`,
  `questions`, `changed_files`, `tests`, `evidence`, `uncertainty`, and
  `next_action`. Completion and report objects reject every other nested key.
- Every `orchestrate` call for a real task must include its exact absolute
  `project_root`. The server is multi-root: do not assume a process-wide root
  binding. A failed, read-only, or mismatched selected root is a hard blocker,
  not permission to fall back to unledgered work.
- A C2/C3 pause needs a handoff at the current gate. A final handoff must
  account for all changed project-relative files under the manifest policy;
  `reconcile_project_files` is useful to find omitted additions, deletions,
  modifications, or renames before handoff.
- Retry-budget exhaustion, a stale attempt, an invalid receipt, or an unknown
  verification id is a repair signal, not permission to dispatch another
  worker. Finalize old attempts, use the exact active receipt/catalog id, and
  create the current-gate handoff before recording `blocked`.
- Lane materialization is limited to declared absolute Git paths and requires
  a live lease plus `confirm=true`; process startup and database setup remain
  outside this executor and require their own implementation/evidence.
- Claims are global across tasks and lanes. Release claims when work ends and
  use an expiry for resources that might outlive a crashed agent. Lane reads
  and task binding are owner-bound; an expired lease needs explicit
  `reclaim=true`. Retiring a lane never deletes an attached pre-existing
  worktree, and managed worktrees must be clean.
- The ledger serializes each mutation and atomically replaces individual JSON
  files, but does not provide cross-file crash atomicity or remote/distributed
  locking. `advance` returns host dispatch instructions; the coordinator then
  invokes native `spawn_agent`, or an explicitly user-authorized
  `create_thread` for a visible task, and supplies the actual host result in a
  later `advance` completion. Native host calls are not MCP public tools.
  The explicitly requested visible-thread route requires the exact
  `create_thread` catalog in `host_capabilities.create_thread_models`, stays on
  the Luna policy route, and preserves the dynamically selected reasoning
  effort rather than forcing `max`. It is never an automatic replacement for
  a hidden subagent. That id is a
  coordinator-recorded correlation, not independent proof from the host. Hooks
  remain best-effort, privacy-limited lifecycle telemetry rather than command
  or spawn proof.
  Visible threads also carry `thread_environment`, which defaults to `local`
  and must be passed as `target.environment.type` to `create_thread`; use
  `worktree` explicitly when isolation is needed. Local threads share the
  saved checkout and uncommitted changes, so concurrent writers must be
  serialized.
- `create_thread` is inherently visible/user-owned; it has no hidden mode.
  Normal model routing keeps `dispatch_mode` at `hidden_subagent`. Pass the
  confirmed `host_capabilities.spawn_agent_default_model` only after the host
  loaded it; otherwise Cortex uses explicit Luna when supported and hidden
  Terra when it is not. `luna_fallback` defaults to and accepts only `terra`.
- If a task is rejected with `orchestration is inactive`, explicitly select a
  non-help Cortex skill route. The skill supplies the server's canonical
  `/cortex` activation token.
- In Desktop, use the Skills picker or `$cortex:orchestrator`; in CLI, lead
  with `$cortex:orchestrator` or use `/skills` and select it.
  `$cortex:orchestrator normal` is
  the supported normal-mode route. Bare `/cortex` and `/normal` (including
  their arguments) are textual shorthand, not native slash-command
  registrations; a host may reserve them.
- Incremental harvest requires a current source-backed coverage manifest with
  zero unexplained gaps; absent, shallow, stale, or contradicted baselines
  trigger a full feature census rather than a recent-change scan. Large
  repositories use 2–8 bounded domain explorers, architecture synthesis,
  non-overlapping documentation owners, behavior-complete feature pages, and
  independent completeness review. Refreshes rebuild the inventory, preserve
  manual text outside generated blocks, require zero unexplained unmapped
  surfaces, and finish only after a no-change second documentation plan.
- A worker report must contain exactly the eight `cortex/report/v1` fields.
  Reports are size/item bounded, sanitized, task-bound, and tied to a real
  delegation attempt. Use an empty list rather than omitting a field; reuse a
  `submission_id` only for an identical payload and mint a new id for a
  correction. C2/C3 evidence consumes its attempt receipt once and only
  `running`/`passed` attempts on the current gate may receive it. Do not send
  late reports after an attempt is terminal or reworked. Listing returns
  metadata; report bodies require explicit context grants.
- Report JSON records are authoritative and Markdown is an escaped generated
  view. A consumed report receipt has an irreversible
  `reports/consumptions/` tombstone. Writes are atomic per file, not across the
  records, receipts, indexes, tombstones, and Markdown files; use
  `reconcile_report_bus` after suspected interruption, but never expect it to
  revive a consumed receipt.
- Routing is binding: `explorer` always selects Luna with coordinator-selected
  effort or the risk default; Terra is only its host-unavailable fallback.
  The accepted effort vocabulary ends at `max`. `planner` and every remaining
  non-security profile default to Luna at exactly `max`, while the coordinator
  may normally choose Terra from `medium` through `max`. Luna `max` is already a
  powerful default and should not be escalated reflexively.
- Host model confirmation is strict: an `advance` completion must include the
  actual `host_model`. Cortex verifies it against `expected_model`, even when
  a configured-default request intentionally omitted native `model`; explicit
  Terra/Sol/Luna overrides remain separate request metadata. A mismatch such
  as expected Luna/actual Terra is rejected during batch preflight rather than
  allowing a false successful result or partial writes.
  A missing host model is recoverable and needs a corrected submission id.
- `expected_model` is durable validation metadata, not a native tool argument.
  For `model_resolution = configured_default`, invoke `spawn_agent` without a
  `model` key and pass `reasoning_effort` independently. Never copy
  `expected_model` into the native request.
- Start-time classification is authoritative; duplicate model-generated
  complexity or requirements fields are ignored rather than compared
  byte-for-byte.
- Host completion and gate proof are separate. A passed attempt may be
  finalized before report evidence is linked, but the gate cannot pass until
  the required evidence exists.
- Advance dispatch preparation accepts human-readable task kinds and
  canonicalizes spaces, hyphens, and case (for example, `Code Review`
  becomes `code_review`); unsafe punctuation remains rejected.
  Security context, the security gate, and the `security_auditor` profile
  always select Sol with minimum effort C1 `medium`, C2 `high`, and C3 `xhigh`.
  Sol effort is also capped at `max`.
  Non-security Sol requires an explicit user choice: pass compact
  `user_requested_model: sol`; omit `model` or set it to `sol`. Cortex records
  matching `user_requested_model` and `requested_model`. Coordinator preference,
  a failed Terra attempt, and auditable-extreme labels are not authorization. The retired
  `sol_escalation` and model/effort remapping contracts must not be sent.
  Profile names come from `plugins/cortex/profiles.json`; there are 21, and
  `task_formatter` is not one of them.
- The installer removes only authenticated known legacy artifacts and backs
  them up. Modified, symlinked, unexpected-version, or unexpected-path targets
  are refused rather than removed. Start a fresh thread after installation so
  the host discovers the new skills, profiles, hooks, and MCP server. During
  the managed Cortex remove/add cycle, an existing
  `plugins."cortex@cortex".mcp_servers.cortex.default_tools_approval_mode`
  override is captured and restored; no override is created when the user did
  not configure one.
- The main orchestrator owns the full optional pipeline: `start` receives the
  complete plan and Cortex appends the mandatory `documentation` and `close`
  audit gates. During work, `advance` may replace future waves under a revision
  guard; changing completed work requires explicit `allow_rework=true`.
- Pipeline gate IDs are canonical lowercase identifiers (`plan`, `discover`,
  `architecture`, `database_architecture`, `implementation`, `qa`, `security`,
  `performance`, `accessibility`, `ux`, `review`, `documentation`, `close`).
  The MCP boundary normalizes a bounded set of human labels such as
  `planning`, `discovery`, and `verification` for adapter compatibility, but
  still rejects unknown IDs instead of guessing.
- Independent gates can be grouped into ordered public `waves`. Only
  gates in the first unfinished wave are executable; each gate is completed
  and evidenced independently, and the next wave cannot start until all gates
  in the current wave are resolved. Keep conflicting writers, shared-resource
  work, and dependency-ordered gates in separate waves.
- An advance completion with a terminal non-passed status needs a reason. A
  final handoff must account for all project paths; incomplete payloads return
  an actionable validation result without a partial durable transition.
- Recovery is bounded. Invalid gate proof is recorded as a recovery event, and
  repeated failures for the same gate/mode eventually block the task with an
  explicit handoff/resume action. Use the same submission id only for an
  identical replay; use a new id for a corrected payload.
