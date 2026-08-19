# Changelog

## [9.2.0] - 2026-08-19

- Remove task-wide report-count and aggregate-byte quotas; reports remain
  individually bounded and SQLite-backed, so long-running tasks may retain
  thousands of immutable handoffs. Successor dispatches now receive the
  verified transitive DAG frontier: every omitted report is covered by a
  passed report that durably acknowledged it, while full history and the
  Planner evidence digest remain intact.

## [9.1.1] - 2026-08-19

- Require every material worker question to reach the coordinator with its
  decision context, self-contained outcome-based choices, trade-offs, and a
  recommendation. The coordinator now explains that context in the user's
  language before opening the native answer control.
- Accept the documented localized batch field names alongside their
  compatibility aliases, render localized option descriptions, and reject
  generic numbered, A/B, or recommended/alternative placeholder choices.

## [9.1.0] - 2026-08-19

- Make orchestration and every supporting overlay explicitly opt-in. The
  orchestrator keeps invocation, harvest routing, isolation, and team policy;
  `cortex-control` is now the single coordinator state-machine and runtime core.
- Compile Worker Briefing v2 from fixed authority/protocol sections, one
  selected-role playbook, conditional mode and phase overlays, and a
  JSON-serialized untrusted Assignment data block. Remove worker-irrelevant
  model/effort and manifest-baseline metadata, duplicated team/profile policy,
  and raw task-value interpolation.
- Move harvest-only specialization out of ordinary agent TOMLs into validated
  conditional overlays, narrow harvest detection to explicit harvest tokens,
  fix discovery dependencies to begin at Scope, and remove obsolete normative
  history from the harvest skill.
- Split automatic recovery into `same_strategy_limit=2` and
  `phase_attempt_limit=3`; a third phase attempt now requires a materially
  different `next_strategy` or a future-wave replan. Add prompt-duplication,
  prompt-injection, overlay-isolation, description, and representative byte-
  budget regression checks.
- Rename the misleading `token-monitoring` skill to `progress-accounting`; its
  policy still forbids collecting token counts, hidden reasoning, or private
  telemetry.

## [9.0.4] - 2026-08-19

- Treat localized batch questions and option IDs as display projections. A
  complete exact canonical key set remains supported, but generated, missing,
  or duplicate display IDs now safely map by canonical position instead of
  rejecting the native user-question form.
- Recover a missing matching parent-session binding from the task's durable
  parent session when a worker stops, while rejecting every different session.
  Every normal stop-hook return now includes an explicit `outcome`.
- Keep deterministic Luna-high verification fixtures explicitly task-scoped,
  so their `continue_orchestration` and resume calls cannot fail closed after
  the task-ref safety change.

## [9.0.3] - 2026-08-19

- Fail a new start closed when the operation registry is incompatible: the
  result is non-retryable and creates neither a task nor a recoverable
  `task_ref`, so a coordinator cannot recover an unrelated older task.
- Require the exact `task_ref` from a successful lifecycle response for every
  task-scoped continue, management, recovery, and report-read call. Cortex no
  longer falls back to a project-wide active task.
- Give every internal worker profile a turn-local read discipline: reuse a
  fully read skill, briefing, source, or report within the turn, reopening it
  only for pagination, an intervening edit, or a distinct unread range.
- Make the final Planner's verified scope/discovery/design report basis
  server-owned, so a compact future-wave request cannot accidentally omit it.
  If a later future-wave validation fails after gate recording, the same
  step/results may retry with corrected `future_waves`, `reason`, or `rework`
  instead of being trapped behind the rejected payload's idempotency receipt.

## [9.0.2] - 2026-08-18

- Reject system and home directories as `project_root` before Cortex begins its
  recursive content-addressed manifest capture. A project root must be a
  specific repository or worktree, preventing an oversized synchronous MCP
  request from appearing hung.
- Make localized durable-question translation self-contained: the public
  `answer` + `answer_en` contract now records a single-question answer directly,
  and `awaiting_translation` returns an exact `translation_request` for either
  a single question or batch. Coordinator guidance explicitly forbids searching
  plugin source/cache or runtime internals to infer public arguments.

## [9.0.1] - 2026-08-18

- Let read-only result gates retain recognized cross-language test, build, and
  cache residue as auditable receipt data instead of rejecting a valid report
  and retrying the same worker. The bounded manifest policy covers conventional
  generated directories, roots, files, bytecode, virtual environments, and
  build outputs, including the common forms that projects put in `.gitignore`.
  Unknown ignored artifacts remain hard failures.

## [9.0.0] - 2026-08-18

- Consolidate report draft validation and persistence in `record_report`.
  Workers now fill the private template and record it directly; callers that
  cannot edit the draft may submit a merge patch or complete replacement.
  Invalid records retain the draft for retry without consuming an attempt, and
  successful records revalidate, atomically persist, then delete it.
- Remove the public `validate_report_draft` operation and `validation_digest`;
  the public contract is now `cortex/orchestration/v5` with exactly eight tools.

All notable public changes to Cortex are recorded here. Release entries use
semantic versions; the plugin manifest adds a unique Codex cachebuster to the
same base version.

## [8.1.2] - 2026-08-18

- Keep caller/input/schema failures from every public worker tool retryable on
  the same worker attempt without consuming the three-attempt recovery budget;
  only explicit non-retryable integrity, storage, permission, or unavailable-
  identity failures end the worker.
- Normalize oversized dispatch-briefing, worker-report, and coordinator
  artifact `max_bytes` requests to the safe 32768-byte SQLite transport bound
  and continue through opaque cursors instead of returning an MCP error.
- Return structured field diagnostics from `worker_question`,
  `get_report_template`, and `read_worker_report`, and recover report numbering
  from the immutable artifact catalog so interrupted index writes do not reuse
  an export path.
- Bind required plan approval to an opaque request ID and expose the pending
  plan through the canonical Approve/Cancel interaction before dispatch.

## [8.1.1] - 2026-08-18

- Make `get_report_template` create one private, fully structured temporary
  JSON report file before validation and return only its `draft_ref`, absolute
  `draft_path`, expiry, and required sections instead of echoing the template.
- Let workers edit that same file directly or apply bounded JSON Merge Patches
  through `validate_report_draft`. Invalid validation keeps the file and never
  consumes the three-failed-attempt recovery budget; success binds a digest to
  the same file without returning its body.
- Make `record_report` reload and atomically revalidate that exact file from
  only worker identity, `draft_ref`, and `validation_digest`, then delete the
  draft and its metadata after the durable report transaction commits. Drafts
  expire after one hour and a new template supersedes the prior attempt draft.
- Treat ordinary source deltas observed by a host-sandboxed read-only worker
  as concurrent shared-workspace evidence instead of an impossible report-JSON
  correction. Claimed `changed_files` and generated or ignored side effects
  remain hard failures for read-only gates.

## [8.1.0] - 2026-08-18

- Add side-effect-free `get_report_template` and `validate_report_draft` worker
  tools. Draft validation returns field paths, concrete fixes, and a digest for
  one unchanged atomically persisted `record_report` payload without consuming
  the three-failed-attempt recovery budget.
- Keep revised Planner reports collision-free by storing every overview under
  its immutable `planning/revisions/plan-<report-ref>/overview.md` revision;
  `planning_current` remains the current-plan pointer.
- Accept globally unique cross-package microtask dependencies while preserving
  unknown-reference and whole-plan cycle rejection.
- Accept concise, non-empty executed-check evidence instead of requiring an
  arbitrary minimum word count that could discard valid QA reports.

## [8.0.0] - 2026-08-18

- Separate evidence-first `scope` from the final `plan`; C2 starts with
  Explorer discovery, while C3 and harvest start with structured Planner
  scoping. Architecture, database architecture, and UX precede the final
  Planner; security, performance, and accessibility remain post-implementation
  audits.
- Bind required plan approval to the plan revision, planner report, verified
  predecessor digest, and semantic future-pipeline digest. Material replanning
  preserves approval history and requires fresh approval; no-op and
  transport-only changes do not invalidate approval.
- Keep the strict seven-field `cortex/report/v1`, v4 public surface, and
  SQLite v8 ledger while adding pipeline contract v2 with v1 resume behavior.
- Make the launcher compatible with stock macOS Bash 3.2, cap sensitive tool
  error logs at 10 MiB with tail-preserving rotation, and require root UI
  questions to use the user's language.
- Precompute Codebase Memory project keys from canonical project roots in every
  worker briefing; routine queries no longer spend a call on `list_projects`,
  which remains a single exact-root fallback for lookup drift or collisions.
- Move runtime-only coordination, ownership, verification, and private-log
  handling rules into the bundled orchestrator/control skills; root
  `AGENTS.md` now contains repository-development policy only.
- This source candidate has not installed or updated a user plugin and has not
  been published, committed, or tagged.

## [7.1.2] - 2026-08-18

- Align the worker report contract across runtime prompts, validators, and
  documentation: `cortex/report/v1` now has exactly seven ordered fields,
  `summary`, `findings`, `questions`, `changed_files`, `tests`, `evidence`,
  and `uncertainty`. `gate_result` and `closure` remain separate top-level
  compatibility siblings.
- Harden report reconciliation, JSON-RPC harness cleanup, Python 3.11
  preflight isolation, and release/version invariants.
- Restore complete Python 3.11/3.12 discovery and offline release gates in CI,
  including marketplace, AST, shell, cold-boot, deterministic fixtures,
  benchmark, conditional fresh-plugin, and tracked-archive checks.
- Source/package evidence for `7.1.2+codex.20260818103113` includes 476-test
  full discovery on Python 3.11 and 3.12, focused `ResourceWarning` coverage,
  cold boot on both supported versions, deterministic fixtures, benchmark, and
  an isolated fresh-plugin probe. Three source-mode live scenarios passed; the
  planner lifecycle completed but its deterministic two-package live rerun is
  still pending, so a complete live PASS is not claimed. The installed user
  plugin remains 6.6.0 and was not changed.

## [1.0.6] - 2026-08-14

- Apply the exact model/effort remapping table in the runtime, including Sol
  routes, and persist the remap metadata in each delegation.
- When the hidden `spawn_agent` catalog lacks Luna but `create_thread` exposes
  it, create a visible Luna task by default instead of silently dispatching a
  Terra subagent. Keep Terra only as an explicit compatibility opt-out.
- Add regression coverage and synchronize routing, fallback, and release
  documentation with the runtime contract.

## [1.0.3] - 2026-08-14

- Add server-level MCP approval guidance and document the token cost of
  automatic approval review.
- Keep visible threads in the saved Local checkout by default, with an
  explicit worktree option for isolation.
- Document hidden `spawn_agent` dispatch versus user-visible `create_thread`
  dispatch and the Luna/Terra fallback trade-off.

## [1.0.1] - 2026-08-14

- Package Cortex as one root marketplace backed by `plugins/cortex`.
- Provide the opt-in Cortex task, gate, report, evidence, and handoff control
  plane with 21 agent profiles and 10 skills.
- Add isolated installation, cold-boot, tracked-archive, redaction, symlink,
  and runtime-state boundary checks.
- Make upgrade backups collision-safe and private.

This entry describes repository readiness; it does not claim that a tag,
remote release, or official catalog listing exists.
