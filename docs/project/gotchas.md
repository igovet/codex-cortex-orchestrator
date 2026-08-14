# Gotchas

- Command evidence must include an explicit `exit_code`; a textual claim that
  a command was green is not sufficient. For a C2/C3 close gate, use
  `execute_verification_command`: only its server-observed exit-0 command
  receipt can satisfy close.
- Bearer tokens, URI credentials, quoted secrets, and secret-like environment
  assignments are redacted before ledger persistence. Do not place real secrets
  in task prompts or reports.
- `claim_resource` is the explicit exclusive-resource API. Use an expiry for
  ports, processes, databases, branches, and other resources that may survive a
  crashed agent.
- An activation is bound to the initialized task and its active-thread mapping;
  it cannot authorize a different task. The mapping is cleaned when a task
  completes. Stale entries from older schema versions should be removed by the
  owning operator after review. Cortex v7 does not read or migrate older tasks
  or lanes; create new records.
- C2/C3 gates cannot silently skip or remove `documentation` or `close` from
  the pipeline. There is no `learn` gate. The documentation gate must be
  delegated to `technical_writer` and record `updated` or justified
  `not_applicable`; close also requires at least one reassessment receipt.
  Other skipped C2/C3 gates require an explicit reason.
- Call `classify_task` before `init_task`; its classification receipt is bound
  to the activated principal, complexity, and requirements. Before every
  delegation, obtain a current `get_task_status` receipt: it is single-use and
  revision-bound.
- Classification runs before a task directory exists, so its one-time receipt
  is staged under the ledger root rather than inside a task directory. It is
  activation-bound, consumed by `init_task`, and linked back to the resulting
  task through `consumed_by`; it is not a worker report or task-owned mutable
  state.
- `principal` and `thread_id` are separate identity fields. Every coordinator
  call should send both. If a native call omits `principal`, Cortex may recover
  it only from the exact task-bound `thread_id`; an unrelated thread is
  rejected.
- Every control-plane call for a real task must include its exact absolute
  `project_root`. Do not touch the project or dispatch a worker until
  activate → classify → `init_task` → `get_task_status` confirms
  `${project_root}/.codex/cortex`. MCP absence, failure, a read-only/mismatched
  root, `CORTEX_ROOT`, or `/tmp` is a hard blocker, not permission to fall
  back to unledgered work.
- A C2/C3 pause needs a handoff at the current gate. A final handoff must
  account for all changed project-relative files under the manifest policy;
  `reconcile_project_files` is useful to find omitted additions, deletions,
  modifications, or renames before handoff.
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
  locking. `record_delegation` leaves an attempt `awaiting_host_spawn`; the
  coordinator must invoke native `spawn_agent`, then use `confirm_host_spawn`
  with the returned child id before it can run. That id is a coordinator-recorded
  correlation, not independent proof from the host. Hooks remain best-effort,
  privacy-limited lifecycle telemetry rather than command or spawn proof.
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
  delegation attempt. C2/C3 evidence consumes its attempt receipt once.
  Listing returns metadata; report bodies require explicit context grants.
- Report JSON records are authoritative and Markdown is an escaped generated
  view. A consumed report receipt has an irreversible
  `reports/consumptions/` tombstone. Writes are atomic per file, not across the
  records, receipts, indexes, tombstones, and Markdown files; use
  `reconcile_report_bus` after suspected interruption, but never expect it to
  revive a consumed receipt.
- Routing is binding: multi-agent v2 is required for explicit Luna dispatch.
  Every delegation is evaluated separately; Luna handles lightweight work at
  low or moderate risk regardless of the task's C1/C2/C3 classification, and
  all other non-security work uses Terra.
- Host model confirmation is strict: `confirm_host_spawn` requires the actual
  `host_model`, verifies it against the requested dispatch model, and marks a
  mismatch such as requested Luna/actual Terra as `host_model_mismatch` rather
  than allowing a false successful Luna result. A missing host model is a
  recoverable response that must be retried with the host's actual model.
- Classification receipts are authoritative. `init_task` ignores duplicate
  model-generated `complexity` and `requirements` fields instead of comparing
  them byte-for-byte.
- Host completion and gate proof are separate. A passed attempt may be
  finalized before report evidence is linked, but the gate cannot pass until
  the required evidence exists.
- `record_delegation` accepts human-readable task kinds from model-generated
  calls and canonicalizes spaces, hyphens, and case (for example, `Code Review`
  becomes `code_review`); unsafe punctuation remains rejected.
  Security task kind, the security gate, and the `security_auditor` profile
  always use Sol, with contradictory task kinds normalized to security.
  Non-security Sol needs
  a structured auditable extreme criterion plus `audit_ref`, or a
  ledger-validated failed Terra attempt; a free-form escalation note is never
  enough. The supported auditable-extreme criteria are
  `irreversible_multi_system_recovery`, `safety_critical_incident_response`,
  and `novel_cross_system_failure_without_bounded_rollback`. Reasoning effort
  is independent of the routing category, except that `none` becomes `low`
  and Sol is clamped to at least `high`.
  Profile names come from `plugins/cortex/profiles.json`; there are 21, and
  `task_formatter` is not one of them.
- The installer removes only authenticated known legacy artifacts and backs
  them up. Modified, symlinked, unexpected-version, or unexpected-path targets
  are refused rather than removed. Start a fresh thread after installation so
  the host discovers the new skills, profiles, hooks, and MCP server.
- The main orchestrator owns the complete optional pipeline: it selects every
  gate except `documentation` and `close` and passes the full list through
  `classify_task.pipeline`. Cortex validates the list and appends only those
  two mandatory audit gates. Calls without `pipeline` use a compatibility
  heuristic, but are not the authoritative orchestrator path. During work,
  `reassess_pipeline` accepts a new full list and applies additions, removals,
  or reordering under a revision guard; removing a completed gate requires
  explicit `allow_rework=true`.
- Independent gates can be grouped into ordered `parallel_groups` waves. Only
  gates in the first unfinished wave are executable; each gate is completed
  and evidenced independently, and the next wave cannot start until all gates
  in the current wave are resolved. Keep conflicting writers, shared-resource
  work, and dependency-ordered gates in separate waves.
- If `finalize_attempt` receives a terminal non-`passed` status without a
  reason, it returns `recorded: false` and
  `next_action: retry_finalize_attempt_with_reason`. This is a recoverable
  protocol response; the attempt is deliberately not finalized until the
  coordinator supplies the reason.
- If `create_handoff` omits changed project paths, it returns
  `recorded: false`, `unaccounted_paths`, and
  `next_action: retry_create_handoff_with_complete_files`. The coordinator
  should add the reported paths and retry; no handoff state is written by the
  incomplete attempt.
