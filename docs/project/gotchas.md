# Gotchas

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
- Expected facade input failures (for example, a missing or relative
  `project_root`, malformed start plan, invalid completion report, or host-model
  mismatch) return a recoverable `orchestrate` result with `ok: false`,
  diagnostics, and `next_action`. They are validated before lifecycle writes
  where possible and are not MCP transport exceptions or tool-error-journal
  events. Correct the payload and use a fresh `submission_id`; reuse an id only
  for a byte-identical replay.
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
- Incremental harvests must not touch current docs without stale or missing
  evidence. Refreshes preserve manual text outside generated blocks and must
  produce a no-change second planning pass before close.
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
- Routing is binding: multi-agent v2 is required for explicit model dispatch.
  Every delegation is evaluated separately from its declared `task_kind` and
  risk. Luna handles explicit reading, discovery/data gathering,
  investigation, diagnosis, research, code review, CRUD-level edits, and
  small fixes at any risk. A read-only profile alone does not imply Luna;
  non-analysis work such as architecture, migration, debugging, and
  implementation initially resolves to Terra and then follows the exact
  model/effort remapping table. Luna analysis/lightweight work defaults to and
  floors at medium effort for low/moderate risk, high for high risk, and xhigh
  for critical risk.
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
  Security task kind, the security gate, and the `security_auditor` profile
  initially resolve to Sol and then follow the exact remapping table, with
  contradictory task kinds normalized to security.
  Non-security Sol needs
  a structured auditable extreme criterion plus `audit_ref`, or a
  ledger-validated failed Terra attempt; a free-form escalation note is never
  enough. The supported auditable-extreme criteria are
  `irreversible_multi_system_recovery`, `safety_critical_incident_response`,
  and `novel_cross_system_failure_without_bounded_rollback`. Reasoning effort
  is independent of the routing category; `none` becomes `low`, and only
  remapping-table pairs bypass the normal Sol high-effort floor.
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
