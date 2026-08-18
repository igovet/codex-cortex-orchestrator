# Security policy

## Supported versions

Security fixes are prepared for the current `8.1.0` source line. The public
contract remains `cortex/orchestration/v4` and the durable ledger remains
SQLite `cortex/v8`. New tasks use pipeline contract v2. Existing active tasks
without that field are treated as v1 and resume their persisted pipeline; they
are not silently migrated or replayed.

## Reporting a vulnerability

Do not include credentials, personal data, private task reports, or exploit
details in a public issue. Use the repository host's private vulnerability
reporting feature when it is explicitly available for the published Cortex
repository. If no confidential reporting channel is shown, contact the
distributor privately through its verified platform identity before sharing
details.

A release must not claim a confidential email address or security contact that
has not been configured and verified. Catalog submission remains blocked until
reviewers can confirm a working private reporting route for the published
repository.

Include the affected version, impact, minimal reproduction, and suggested
remediation. Never attach a real token, key, user ledger, backup, or customer
export. Maintainers should acknowledge a report, coordinate a remediation and
disclosure timeline, and publish a new cachebusted version rather than mutate
an existing public tag.

## Security boundaries

Cortex writes its durable ledger only below the selected project's
`.codex/cortex` directory. The release excludes runtime state, bytecode,
symlinks, and secret-prone paths. The installer backs up only authenticated
legacy targets and refuses unexpected paths or symlink ancestry.

Worker prompts are immutable, read-only briefing artifacts addressed by an
exact path and SHA-256 digest. Native dispatch carries only the compact
identity/bootstrap needed to retrieve that briefing; workers cannot browse
other ledger artifacts. Reports retain the strict seven-field
`cortex/report/v1` contract. The sensitive diagnostic log at
`~/.codex/logs/cortex-tool-errors.jsonl` is permission-protected and capped at
10 MiB by retaining complete newest records and dropping the oldest records
first. Secrets, credentials, personal data, and private report contents must
never be placed in prompts, reports, issues, or logs.

Required plan approval is bound to a specific plan revision, planner report,
verified predecessor evidence digest, and semantic future-pipeline digest. A
material replan preserves the prior approval in history and requires a new
approval; no-op or transport-only changes do not invalidate it. A mismatch
blocks dispatch with recoverable reapproval guidance.

Questions shown to users by the root coordinator must be localized to the
user's original language. Worker protocol messages and durable worker reports
remain English, and localized UI text must not alter the canonical question or
answer values.

The archive boundary is validated from `git archive HEAD`, not the mutable
working tree. A repository with an unborn `HEAD` has no release archive to
validate: `--require-tracked` must remain a publication blocker until an
authorized initial commit exists and the check passes against it.

This working tree is a source candidate only. The 8.1.0+codex.20260818200000
changes are not installed into a user's plugin, published, committed, or
tagged by this task.
