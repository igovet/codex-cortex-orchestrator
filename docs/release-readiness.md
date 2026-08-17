# Release readiness

This document records the repository-side gates for a public Cortex release.
It does not claim that a commit, tag, remote, catalog submission, or catalog
approval exists.

## Package contract

- `.agents/plugins/marketplace.json` is the only marketplace manifest.
- `plugins/cortex` is the only installable Cortex source tree.
- Root development scripts, tests, and documentation support the package but
  are not duplicate installable agent or skill sources.
- The plugin and MCP server versions must match the release contract
  `6.5.0` (an installed build may carry a `+codex.<build>` suffix).
- Runtime selection is fail-closed: set `CORTEX_PYTHON` to one absolute
  executable path for Python 3.11+ with `tomllib`, or leave it unset to resolve
  `python3` from `PATH`. The installer, MCP server, and lifecycle hooks use the
  same selection, and an invalid explicit path must not mutate Codex
  configuration.
- Optional public manifest fields are not added until their exact names and
  shapes are verified against the installed or official Codex schema. The
  current release work does not invent repository, homepage, license, privacy,
  screenshot, or prompt-array metadata.

## Trust boundaries

The tracked release is built from `git archive HEAD`, never from mutable local
runtime state. Validation rejects symlinks, hard links, path traversal, the
retired nested marketplace, `.codex` state, bytecode, secret-prone filenames
and credential-store paths, missing public policy documents, private local
home paths in public files, and explicit release placeholders.

The installer validates `HOME` and `CODEX_HOME` ancestry, preserves the user
MCP approval override, and creates a collision-safe private backup only before
changing a configured global default-subagent model. It never inspects or
removes previous orchestration state or unrelated plugin files.

## Required repository checks

Run the commands in `docs/project/verification.md`. A release candidate must
pass the full regression suite, marketplace validation, Python and shell syntax
checks, cold-boot smoke test, isolated fresh-plugin probe, and the blocking
tracked-release archive validation.

The 6.5.0 source candidate must be verified by the full test suite,
marketplace validation, Python compilation, shell syntax, the isolated
fresh-plugin probe, and installed-content verification at
`6.5.0+codex.20260816170348`. The final Luna-high automatic-sequential live
run for that installed build must be recorded with close evidence, handoff,
and snapshot cleanup. File-size hardening covers the 8 MiB ordinary-JSON
bound with fail-before-replace diagnostics, the separate 64 MiB manifest
bound, bounded handoff/reconciliation snapshots, and fail-closed diagnostics
for oversized artifacts. The 6.5.0 ledger starts from SQLite only: its
checksummed migrations operate SQLite-to-SQLite, while pre-SQLite task files
are left untouched and never become coordination state. Installation preserves
the user MCP approval override. Live-model and tracked-release validation are
split deliberately; the live result and the post-commit archive result are
recorded in `docs/project/verification.md` before push.
Tag, catalog submission, approval, and public publication are not part of this
local plugin update and are not claimed.

## External release gates

- Create the Cortex 6.5.0 release commit only with explicit authorization.
- Rerun `python3 scripts/verify-cortex-release.py --require-tracked` against the
  real committed tree; an unborn `HEAD` is a release blocker.
- Verify any optional public manifest metadata against the current official or
  installed Codex schema before adding it.
- Configure and verify a confidential security reporting route without placing
  personal contact data in the repository.
- Verify a clean installation from the immutable Git tag in fresh `HOME` and
  `CODEX_HOME` directories.
- Establish the authorized public remote provenance, then review `git ls-files`
  and the final archive inventory before push, tag, or catalog submission.
- Obtain the catalog's required approval or authorization; local marketplace
  registration and an isolated CLI probe do not establish catalog availability.

Failed external gates block publication. Existing tags are never rewritten to
repair a release; publish a new cachebusted version with a changelog entry.
