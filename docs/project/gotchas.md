# Gotchas

## Canonical runtime artifacts

- New tasks use the SQLite-backed `cortex/v8` ledger and public
  `cortex/orchestration/v4` lifecycle. On the first MCP access after a source
  version introduces a migration, numbered migrations run in one fail-closed transaction and are
  recorded in `schema_migrations`. Pre-database filesystem state is ignored:
  it is neither imported nor resumed. The runtime does not create
  `v3-operations.json`, active-task or
  status-receipt files, `reports/grants`, `metrics.json`, task lock files,
  handoff-manifest snapshots, or evidence-snapshot files.
- Migration checksums are content-based SHA-256 digests of each migration's
  version, name, and ordered normalized SQL statements. A legacy name-only
  checksum is upgraded only after the corresponding schema is validated;
  tampered history, missing objects, changed statements, and inconsistent
  `user_version` state fail closed.
- New task-start and per-attempt baselines are immutable, content-addressed
  records in `cortex.db`. State and attempts retain compact
  `manifest-<sha256>` refs instead of copying paths or manifest bodies.
  Identical project state deduplicates, but every dispatch still captures the
  project again to detect external changes. These snapshots reconcile actual
  `changed_files` and prevent read-only workers claiming writes in non-Git and
  pre-existing-dirty repositories, where Git HEAD/index is insufficient.
- After terminal completion is persisted, Cortex removes manifest records.
  Final receipts retain manifest digests and change proof. If
  `allow_rework` reopens that task, Cortex first captures a fresh active
  baseline; stale deleted snapshots are never reused.
- Schema v8 adds task/plan revisions, native worker sessions, attempt messages,
  trace/tool observations, and question-batch storage. Schema v7 does not use
  one artifact identity for everything. A content blob
  may be shared by digest, a logical artifact is task-scoped, and an export
  path is separately authorized. Do not use a filesystem path as canonical
  evidence or infer a blob's lifecycle from an optional projection.
- Projection jobs are durable outbox rows, not background hints. A caller must
  not write an export directly or acknowledge a job without its lease and
  digest verification. Required briefing projections are capabilities; all
  other projection files remain rebuildable.
- Tool-observation dedupe is scoped to task, attempt, context epoch,
  normalized fingerprint, and workspace generation. Only a successful
  full-coverage observation is reusable; duplicate calls are counted and
  partial coverage never authorizes reuse.
- Every gate report must publish the top-level structured `gate_result` sibling, not a
  ninth member of the strict report. It is canonical for all gates; the older
  `closure` sibling is only a review/close compatibility alias. An unresolved
  P0/P1/P2 or blocking finding, or a missing required check, takes the recorded
  gate back through rework. A waiver needs reason, actor, and timestamp and
  cannot be self-issued.
- Prune is deliberately two-phase: a tombstone commits before filesystem work;
  canonical rows remain until that work succeeds. Never remove task records,
  WAL, or SHM directly to "help" a failed prune. Use explicit legacy
  archive/delete maintenance for pre-SQLite files and SQLite-aware maintenance
  for health, backup, checkpoint, or projection repair.

## Public v4 coordinator contract

- Normal flow is `start_orchestration` once, then
  `continue_orchestration` once per completed relative `step`.
  `manage_orchestration` is only for inspect/resume/deactivate and rare
  lane/resource/durable-question work. Earlier orchestration entrypoints are
  not public runtime interfaces. Together with worker
  `worker_question`/`record_report`, identity/digest-scoped
  `read_dispatch_briefing`, and predecessor-only `read_worker_report`, the
  public surface is exactly seven tools.
- Every public call requires the exact absolute `project_root`. Start requires
  the user's exact, unexpanded `task.user_request`; the sole host-metadata
  exception is Desktop's injected `$cortex:orchestrator` wrapper, which is
  canonicalized to `$cortex:orchestrator` before task identity, labels,
  persistence, and worker prompts. The route and following user text are
  preserved; arbitrary links and user paths are not normalized. Deprecated
  `task.objective` is only an exact compatibility mirror. Complexity defaults to C2. Compact
  wave overrides use `waves[].workers[]`, with only `phase` required.
- Continue carries the prior response's relative `step`. A single result omits
  `worker`; a parallel wave must return every unique integer worker slot once.
  Stale, duplicate, missing, foreign, or changed retries fail before task-state
  writes.
- Successful results carry a compact `report_ref`. Workers persist exactly
  these ordered seven `cortex/report/v1` fields through the scoped public
  `record_report` tool: `summary`, `findings`, `questions`, `changed_files`,
  `tests`, `evidence`, and `uncertainty`.
  then return only `REPORT_RECORDED report_ref=<value>` plus at most a
  two-sentence summary. The coordinator reads the full report through
  `read_worker_report`; successor workers may use that tool only for explicitly
  supplied predecessor refs with exact scope identifiers. Non-success results omit report refs and require a
  normalized status plus reason. Workers never call lifecycle, pipeline,
  gate, delegation, or management operations and never paste report JSON into
  the native parent result. If `record_report` fails, the native final contains
  only the exact report-tool error. If persistence succeeds
  but the native acknowledgement is interrupted, inspect with
  `manage_orchestration` and recover the ref from `available_reports`.
- Any profile may call `worker_question(action="ask")` for a material user
  decision that repository evidence cannot resolve. It returns only a compact
  question ref to the parent and stays alive; the coordinator surfaces the ref
  through management, obtains the user answer, then resumes that same worker,
  which polls the ref. Open blocking questions reject both `record_report` and
  `continue_orchestration`. Do not encode missing product intent as assumptions.
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
- Caller-correctable `record_report` failures (identity, generated evidence
  acknowledgements, safe `changed_files`, harvest-manifest shape, or report
  payload) return structured `ok: false` diagnostics and do not enter the
  private exception journal. Genuine ledger/server corruption remains an
  exception and is logged for diagnosis.
- Idempotency is server-owned. Callers do not send submission, task, wave,
  attempt, coordinator identity, or host metadata. Preserve the returned opaque
  `task_ref` on every later lifecycle and report-read call. Exact duplicate
  active starts replay; changed task/wave contracts create distinct concurrent
  tasks below the same project root. Omitting the ref while several tasks are
  selectable returns `needs_selection` with opaque candidates instead of a
  guessed active task.
- A successful start response includes `replayed`. Once start returns
  dispatches, never call it again for that `task_ref`; a replay is a receipt,
  contains no dispatches, and cannot authorize a duplicate wave. A genuinely
  lost first response is recovered once through management inspect.
- Active corrections use `manage_orchestration(intent="steer")` with the
  original `user_message` and canonical English `message_en` when needed.
  Cortex records a task revision and resumes only addressable native workers
  through returned `followup_task` calls. A completed source is immutable and
  uses linked `follow_up`; these routes must not be conflated.
- `prune` is the only cleanup route. It requires exact confirmation `PRUNE`,
  defaults to seven days, omits `task_ref`, and removes only completed
  task-scoped Cortex state older than the threshold. Active and blocked tasks
  survive regardless of age. Classification receipts referenced by any
  retained task also survive. Recent completed tasks, lanes, source, docs, and
  plugin files are preserved; there is no clear-all operation.
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
- Localized question labels are transient UI projections. Answers retain the
  original value/language and require canonical `answer_en` for localized free
  text. `ask_batch` accepts 1–32 stable questions but the native UI renders
  only one step at a time under one durable `batch_ref`; each accepted step is
  checkpointed, and cancellation resumes at the next unanswered question.
  `poll_batch` returns canonical English answers; an active task revision
  supersedes an unresolved batch so stale intent cannot resume a worker.
- Fixture Luna-high evaluation covers sequential, compact parallel, and
  blocked/resume flows. The live evaluator is source-mode only: it launches
  `codex exec --ephemeral --ignore-user-config` against this checkout's MCP
  server with per-run private 0700 `HOME`/`CODEX_HOME` and temporary/cache/config/data
  directories. It passes only least-privilege runtime variables and a selected
  `OPENAI_API_KEY`/`CODEX_API_KEY`; otherwise it copies a private regular
  non-symlink `auth.json` (at most 1 MiB) through no-follow checks to 0600
  storage, without logging credentials. It never calls the installer or
  changes global Codex configuration. It streams only bounded
  `cortex_live_progress` JSON classifications and aggregate ledger counts;
  prompts, arguments, results, source paths, and arbitrary host diagnostic
  content are redacted. Heartbeats are emitted every 15 seconds while the parent runs, and
  the per-scenario timeout defaults to 1,800 seconds (`--live-timeout-seconds`
  accepts 10..7200). Timeout or interruption terminates the complete process
  group, escalating from `SIGTERM` to `SIGKILL` after ten seconds. Normal or
  supervised exits clean the private runtime and temporary project; a crash or
  external `SIGKILL` may leave OS-temp residue. A live
  `SKIP` means missing release evidence, not pass. Failed scenarios retain a
  sanitized `progress.json` only when the explicit
  `--retain-failure-metadata` opt-in is supplied; otherwise the result marks
  failure metadata `not_retained`. Raw event content is not retained. Use the
  fresh-plugin probe, `sync-cortex.sh --check`, and
  tracked-release verification for installed/package evidence.
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
  Hidden `spawn_agent` arguments intentionally include `fork_turns: "none"`;
  do not replace it with inherited coordinator context.
- Dispatches should be issued in one model turn when the host supports
  parallel calls. Correlate each start by exact `task_name`/`dispatch_ref` and
  host child id, never by ordinal or display label. While the wave is active,
  `waiting_workers` carries `output_policy="silent"`; repeated wait timeouts
  must not generate heartbeat commentary.
- Keep `profile` as the exact canonical role name, and use the human-readable
  `display_name` is derived from the task domain in the user's request (for
  example, `Planner Authentication`), without an ordinal or digest. Gate
  mission verbs are not used as the display module. The unique native
  `spawn_agent.task_name` carries the lower-
  underscore profile/module, ordinal, and digest (for example,
  `explorer_auth_02_<digest>`); `followup_task` resumes that same native
  worker after a durable question or active steer, never a dead replacement.
  Host spawn prompts de-duplicate the exact user request; `start_orchestration.next_action`
  is serialized before dispatch payloads. The nested realistic harvest Planner
  prompt is regression-tested below 11,500 bytes, the compact native bootstrap
  is below 1,500 bytes, and the complete public start response below 8,000
  UTF-8 bytes. A worker is not sent until native
  `spawn_agent` returns a child id; empty dispatch announcements or waits are
  forbidden, and a synchronous `PreToolUse` hook denies a targetless wait
  before it can block. Native failure is a blocker. The native
  `spawn_agent.task_name` is the lower-underscore equivalent with a
  uniqueness digest and is a task/attempt-unique session key that must match
  the host's strict `[a-z0-9_]{1,80}` contract. Its deterministic digest
  preserves uniqueness without copying durable IDs, skill paths, or prompt
  text into the host-visible name. Durable Cortex IDs may still contain
  hyphens.
  Hooks map that key (or its confirmed host alias) back to the canonical
  profile. Use `followup_task` only for that exact resumed worker;
  `host_agent_id` reuse is rejected across attempts.
- A native child that stops before publishing a report or durable question is
  terminal, not follow-up-resumable. After one `manage_orchestration` inspect,
  submit exactly one failed continuation with the stopped attempt's
  `dispatch_ref`, `status="failed"`, and
  `reason="native_worker_stopped_without_report"`; wait, respawn, and
  `followup_task` are invalid for that child. Only the fresh top-level
  dispatch returned by Cortex may retry, and the third failure blocks with a
  durable handoff. PostToolUse recovery scans all non-invalidated reportless
  attempts in the current gate, not only the last attempt, so a later
  completed retry does not hide an earlier failure receipt that is still
  required.
- Once Cortex is active, the main/root agent is coordination-only. It must not
  inspect, search, read, edit, patch, build, test, or run the target project,
  even when a worker is delayed, fails, or is unavailable. Dispatch only the
  workers returned by Cortex, remain idle while they run, and use recovery,
  rework, or a blocker instead of taking over their project work. `SessionStart`
  and every public v4 `next_action` repeat this rule so compaction or a resumed
  turn does not weaken it.
- After context compaction, do not trust the visible transcript or assume the
  loaded skill cache is current. Preserve the opaque `task_ref`, call
  `manage_orchestration(intent="inspect")` once, and rehydrate from its
  `context_handoff`. It is ledger-derived recovery state, not a replacement
  for the orchestrator skill; never restart the task or replay completed work.
- New v4 starts use a generated task-local authorization identity, then the
  synchronous Cortex `PostToolUse` hook binds the returned `task_ref` to the
  documented event `session_id`. Explicitly forwarded `CODEX_SESSION_ID` or
  `CODEX_THREAD_ID` values are compatibility hints only. `SessionStart` handles
  `resume`, `clear`, and `compact`; if multiple active tasks share the session,
  its lookup is removed until one remains rather than guessing.
- Plugin installation and reload are operator-owned. After installation or an
  update, start a fresh Codex thread; an existing thread may retain retired
  cachebusted hook paths and will not load updated skills, hooks, or MCP tools
  automatically.
- Codebase Memory is worker-only and conditional. When its tools are present,
  call `list_projects` first and use only the project whose root exactly matches
  the task root; prefer graph, architecture, and trace operations, then confirm
  consequential facts in source/tests. `planner`, `explorer`, `architect`, and
  `database_architect` may refresh one missing or stale index; other profiles
  fall back to repository-native tools after one failed attempt. Never guess a
  project id or loop. An indexed repository never authorizes the root
  coordinator to inspect the project.

## Internal ledger invariants

The remaining notes document internal invariants behind the public v4 lifecycle
and v8 ledger. They are not caller-facing request envelopes.

- Command evidence must include an explicit `exit_code`; a textual claim that
  a command was green is not sufficient. Every `report.tests` entry for a
  successfully completed executed-check gate must have integer `exit_code: 0`.
  Negative-path verification must use an assertion harness that observes the
  expected rejection and exits 0. Never omit, disguise, relabel, or balance a
  nonzero check with another passing check: report intake returns
  `worker_verification_failed`, and the gate must be repaired and rerun or sent
  through fresh Cortex-authorized rework. The final C2/C3 `advance` privately
  runs the allowlisted close verification and requires its server-observed
  exit-0 receipt; coordinators must not call the private verification primitive.
- Bearer tokens, URI credentials, quoted secrets, and secret-like environment
  assignments are redacted before ledger persistence. Do not place real secrets
  in task prompts or reports.
- `orchestrate(operation="resource", payload.command="claim")` is the explicit
  exclusive-resource API. Use an expiry for ports, processes, databases,
  branches, and other resources that may survive a crashed agent.
- C2/C3 gates cannot silently skip or remove `documentation` or `close` from
  the pipeline. There is no `learn` gate. The documentation gate must be
  delegated to `technical_writer` and record `updated` or justified
  `not_applicable`; close also requires at least one reassessment receipt.
  Other skipped C2/C3 gates require an explicit reason.
- Use `advance` to submit a reviewed future-wave replacement. A no-op remains
  unchanged; never claim a C2/C3 documentation or reassessment decision was
  applied without the corresponding durable operation. Replacing completed
  work requires `allow_rework=true`. If final-close evidence supplies an
  explicit replacement, Cortex reopens the internally completed pipeline and
  invalidates downstream attempts, evidence records, and report receipts before
  dispatching the replacement waves; returning terminal success while
  those waves remain would be a false completion.
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
- Expected public v4 validation and recovery failures return structured
  `ok: false` results with bounded `diagnostics` and a corrective `next_action`.
  They are caller-correctable protocol outcomes and are not written to
  `~/.codex/logs/cortex-tool-errors.jsonl`; only raised MCP-boundary exceptions
  enter that private redacted log. Public v4 validation occurs before lifecycle
  writes where possible. Correct every diagnostic and retry according to the
  returned action.
- Preflight aggregates independent request mistakes into one `ok: false`
  response. Each diagnostic has `path`, `message`, and `expected`; repair every
  listed path before retrying. Do not treat the first diagnostic as the only
  error or submit a sequence of one-field fixes.
- The exact lifecycle start envelope is `{operation:"start", project_root, principal,
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
  `questions`, `changed_files`, `tests`, `evidence`, and `uncertainty`.
  Completion and report objects reject every other nested key.
- Every `orchestrate` call for a real task must include its exact absolute
  `project_root`. The server is multi-root: do not assume a process-wide root
  binding. A failed, read-only, or mismatched selected root is a hard blocker,
  not permission to fall back to unledgered work.
- A C2/C3 pause needs a handoff at the current gate. A final handoff must
  account for all changed project-relative files under the manifest policy;
  `reconcile_project_files` is useful to find omitted additions, deletions,
  modifications, or renames before handoff.
- Baseline manifests honor the project's `.gitignore` rules, including ordered
  negations, and persist the effective rules in the baseline policy. The same
  frozen policy is reused during reconciliation, so changing `.gitignore`
  during an active task does not silently redefine its scope. Cortex also
  excludes high-confidence dependency/cache/runtime directories such as
  `node_modules`, `.pnpm-store`, `.venv*`, and language-specific caches. Generic
  `build`, `dist`, `target`, `bin`, and `obj` directories require an applicable
  ignore rule or recognizable generated-output marker; do not assume every
  directory with one of those names is ignored.
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
  non-help Cortex skill route. Use the Skills picker or `$cortex:orchestrator`;
  never ask the user to send a bare `/cortex` token as a recovery command.
- In Desktop, use the Skills picker or `$cortex:orchestrator`; in CLI, lead
  with `$cortex:orchestrator` or use `/skills` and select it.
  `$cortex:orchestrator normal` is
  the supported normal-mode route. Bare `/cortex` and `/normal` (including
  their arguments) are textual shorthand, not native slash-command
  registrations; a host may reserve them.
- A completed-source `follow_up` is idempotent. If its existing corrective
  task is replayed after a coordinator deactivation, the server restores the
  task-scoped activation and returns the existing task without dispatching a
  duplicate. Continue with that opaque `task_ref`; do not reopen the source or
  expose an internal activation diagnostic.
- Incremental harvest requires a current source-backed coverage manifest with
  zero unexplained gaps; absent, shallow, stale, or contradicted baselines
  trigger a full feature census rather than a recent-change scan. Large
  repositories use 2–8 bounded domain explorers, architecture synthesis,
  non-overlapping documentation owners, behavior-complete feature pages, and
  independent completeness review. Refreshes rebuild the inventory, preserve
  manual text outside generated blocks, require zero unexplained unmapped
  surfaces, and finish only after a no-change second documentation plan.
- A worker report must contain exactly the seven ordered `cortex/report/v1`
  fields: `summary`, `findings`, `questions`, `changed_files`, `tests`,
  `evidence`, and `uncertainty`.
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
  Security always selects Sol. The machine-validated ordinary policy classifies
  profiles as efficient, adaptive, or deep: efficient work uses Luna, deep
  profiles use Terra, and adaptive work uses Luna for low/moderate-risk
  work without a `terra_task_kinds` trigger. C2/C3 planning, uncertain
  diagnosis, long context, integration conflict, and high/critical failure cost
  use Terra.
  Efficient Luna uses `high`/`high`/`xhigh`, bounded adaptive Luna uses
  `high`/`xhigh`/`max`, and Terra uses `high`/`high`/`xhigh` across C1/C2/C3.
  Automatic `max` is limited to bounded C3 Luna work. Explicit Luna/Terra overrides
  cannot lower the computed effort floor.
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
- The installer does not inspect or alter prior orchestration state or
  unrelated plugin files. Start a fresh thread after installation so the host
  discovers the new skills, profiles, hooks, and MCP server. During the managed
  Cortex remove/add cycle, an existing
  `plugins."cortex@cortex".mcp_servers.cortex.default_tools_approval_mode`
  override is captured and restored during remove/add, then the installer
  enforces `default_tools_approval_mode = "approve"` even on a clean install.
  `--check` fails when the effective value is absent or weaker than `approve`.
- Hook trust is checked independently of plugin file matching. An explicit
  sync queries Codex `hooks/list` and accepts only the five enabled
  `cortex@cortex` hooks from the installed cache, with the installed command
  path and a valid `sha256:` content hash. `sync-cortex.sh --check` repeats
  this exact identity/hash check. Install refreshes stale trust only after the
  installed plugin content matches the selected release; `--check` fails when
  a `PreToolUse` or `PostToolUse` identity or hash drifts instead of allowing
  silent worker-binding corruption. Start a new thread after a successful
  update; existing threads still point at the old cachebusted paths.
- A missing host-session binding is recoverable from `SubagentStart` only when
  exactly one active pending dispatch matches the exact native task key, or
  the generic `default` event plus its observed model. Ambiguous, missing, or
  model-mismatched matches fail closed; the hook never guesses which worker to
  bind.
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
