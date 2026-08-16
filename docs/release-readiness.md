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
  `6.1.1` (an installed build may carry a `+codex.<build>` suffix).
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

The installer validates `HOME` and `CODEX_HOME` ancestry, authenticates exact
legacy cleanup targets, creates collision-safe upgrade backups, enforces mode
`0700` on the Cortex backup directory, and removes group/world permissions from
each backup slot. Backups are local operator data with manual retention.

## Required repository checks

Run the commands in `docs/project/verification.md`. A release candidate must
pass the full regression suite, marketplace validation, Python and shell syntax
checks, cold-boot smoke test, isolated fresh-plugin probe, and the blocking
tracked-release archive validation.

Current 6.1.1 source evidence includes the full passing test suite, marketplace
validation, Python compilation, shell syntax, the isolated fresh-plugin probe,
and installed-content verification at `6.1.1+codex.<build>`. File-size hardening covers
the 8 MiB ordinary-JSON bound with fail-before-replace diagnostics, the
separate 64 MiB manifest bound, early baseline preflight, bounded
handoff/reconciliation snapshots, and actionable fail-closed errors for
oversized artifacts. A copy-based registry migration and the Planner prompt
measurements are recorded in `docs/project/verification.md`. Installation
preserved the user MCP approval override. Live-model and tracked-release
validation are split deliberately: native live execution completed the full
six-phase harvest and a fresh final-build dispatch confirmed the corrected
human/native worker identities; the tracked-release archive check runs only
after the candidate is committed and must pass before push.
Historical 4.0.0 installation and validation evidence does not attest this
patch.
Tag, catalog submission, approval, and public publication are not part of this
local plugin update and are not claimed.

## External release gates

- Create the Cortex 6.1.1 release commit only with explicit authorization.
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
