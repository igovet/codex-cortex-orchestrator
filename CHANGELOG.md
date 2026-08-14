# Changelog

All notable public changes to Cortex are recorded here. Release entries use
semantic versions; the plugin manifest adds a unique Codex cachebuster to the
same base version.

## [Unreleased]

- Let visible Cortex threads request the saved Local checkout by default, with
  an explicit worktree option for isolation.
- Catalog submission, remote provenance, and tagged installation remain
  pending external release authorization and verification.
- This checkout has an unborn `HEAD`; a committed release tree and a passing
  `verify-cortex-release.py --require-tracked` check remain prerequisites to
  any publication claim.

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
