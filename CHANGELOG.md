# Changelog

All notable public changes to Cortex are recorded here. Release entries use
semantic versions; the plugin manifest adds a unique Codex cachebuster to the
same base version.

## [Unreleased]

- Replace the v2 `orchestrate` facade with the relative v3 public tools
  `start_orchestration`, `continue_orchestration`, and
  `manage_orchestration`; keep v7 lifecycle state and receipts private for
  ledger compatibility.
- Make the minimal start contract just `project_root` plus `task.objective`,
  default to C2, normalize human-readable phase/profile/complexity aliases,
  normalize common language names before ledger creation, build the standard pipeline automatically, and accept compact wave
  overrides without durable wave identifiers.
- Make continuation relative to the returned `step`, omit worker references
  for sequential waves, use short `worker: 1..N` slots for parallel waves,
  and atomically reject missing, duplicate, foreign, stale, or malformed
  results before lifecycle writes.
- Move idempotency ownership to the server, replay identical retries, reject
  changed or stale payloads, recover checkpointed transactions, and avoid
  duplicate active tasks when Luna repeats a start with revised wording.
- Treat semantically unchanged future-wave reassessment as a valid unchanged
  receipt and keep relative future steps monotonic after replacement.
- Return compact native dispatches without internal task/wave/attempt IDs;
  keep expected routing separate from actual host attestation and never copy
  an expected configured-default Luna model into native `model`.
- Simplify the worker contract to the strict eight-section parent result,
  remove private `record_report` and generic identifier-heavy tool guidance,
  and retire unsuccessful attempts before a fresh relative retry.
- Keep inspect/resume/deactivate/lane/resource/question on the rare management
  path and add regression coverage for the modern Codex
  `extensions["openai/form"]` elicitation capability.
- Add v3 cold-boot, benchmark, fixture Luna-high, and opt-in live Luna-high
  evaluation. A skipped live run is explicitly missing release evidence, not
  a pass.
- Route omitted Luna model overrides through the configured global
  `agents.default_subagent_model`, keep expected-model metadata separate from
  native requests, and make the installer enforce the default atomically with
  a private backup before replacing another value plus dry-run/check coverage.
- Remove automatic visible `create_thread` fallback. An unavailable hidden
  Luna route now stays hidden and uses an explicit Terra override; visible
  threads remain an independently requested workflow only.
- Let visible Cortex threads request the saved Local checkout by default, with
  an explicit worktree option for isolation.
- Harden coordinator/worker protocol guidance for bound identities,
  preview/apply reassessment, stale-attempt recovery, strict report retries,
  evidence receipts, mandatory gates, and blocked-gate handoffs. Confirmed host
  child ids are now accepted as aliases only for their own worker reports.
- Add aggregate private-facade preflight diagnostics with exact nested `start` and
  `advance` schemas, and record every private `orchestrate` `ok: false` result in the
  redacted Cortex tool-error journal.
- Catalog submission, remote provenance, and tagged installation remain
  pending external release authorization and verification.
- The 3.0 working-tree changes remain uncommitted; a committed release tree and
  a passing `verify-cortex-release.py --require-tracked` check remain
  prerequisites to any publication claim.

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
- Provide the opt-in `cortex/v7` task, gate, report, evidence, and handoff
  control plane with 21 agent profiles and 10 skills.
- Add isolated installation, cold-boot, tracked-archive, redaction, symlink,
  and runtime-state boundary checks.
- Make upgrade backups collision-safe and private.

This entry describes repository readiness; it does not claim that a tag,
remote release, or official catalog listing exists.
