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
  `8.1.1` (the current candidate is
  `8.1.1+codex.20260818210000`; installed builds may carry a cachebuster).
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

Report finalization uses a private pre-validation file: `get_report_template`
creates a fully structured JSON file with mode `0600` and returns `draft_ref`,
`draft_path`, and expiry without returning the body. Writers edit that exact
file; read-only workers may send a small RFC 7396 merge patch. Invalid
`validate_report_draft` calls leave the same file in place and consume no
attempt; successful validation binds its digest to that file. A new template
supersedes an old or expired draft. `record_report` rereads/revalidates and
deletes the file and metadata only after commit. Normal callers send only
identity, ref, and digest; legacy full-payload recording remains compatible.
Host-sandboxed read-only gates treat ordinary shared-checkout source deltas as
concurrency evidence, while claimed changes and generated or ignored side
effects remain failures.

## Required repository checks

Run the commands in `docs/project/verification.md`. A release candidate must
pass the full regression suite, marketplace validation, Python and shell syntax
checks, cold-boot smoke test, isolated fresh-plugin probe, and the blocking
tracked-release archive validation.

The read-only host gate is separate from source and archive evidence:
`cortex-host-preflight.py --json` must report `mcp.status=READY` only for the
same Codex user with a matching enabled `cortex@cortex` registration, approval
configuration, cache-backed hook trust, and all other prerequisite checks.
The named `Hetzner_Bots` host remains blocked until an approved Node >=16
installation source is available; no guessed package-source command is a
release step. Follow [SSH host troubleshooting](project/ssh-hetzner-troubleshooting.md)
for the safe same-user sequence and the bounded stopped-worker recovery.

The 8.1.1 source candidate has passed all 494 unit tests on Python 3.12.3,
focused report/schema/read-only tests, cold boot, marketplace validation,
Python and shell syntax checks, `git diff --check`, the composite benchmark,
and an isolated fresh-plugin probe. The installed-plugin check, full lifecycle
live scenario, and tracked archive gate have not been run. The installed user plugin is out
of scope; no installation or `~/.codex` mutation is part of this candidate. The evidence-first pipeline,
scope artifact, plan-basis digests, v1 resume compatibility, 10 MiB
tail-preserving error-log cap, and Bash 3.2 launcher compatibility require
focused regression coverage. The 8.1.1
ledger starts from SQLite only: its
checksummed migrations operate SQLite-to-SQLite, while pre-SQLite task files
are left untouched and never become coordination state. Installation preserves
the user MCP approval override. Targeted development validation, full
lifecycle live-model validation, and tracked-release validation are split
deliberately; the remaining release results and the post-commit archive result
are recorded in `docs/project/verification.md` before push.
Tag, catalog submission, approval, and public publication are not part of this
local plugin update and are not claimed.

## External release gates

- Create the Cortex 8.1.1 release commit only with explicit authorization.
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
