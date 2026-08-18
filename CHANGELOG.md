# Changelog

All notable public changes to Cortex are recorded here. Release entries use
semantic versions; the plugin manifest adds a unique Codex cachebuster to the
same base version.

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
