# Privacy policy

Cortex is a local Codex plugin. It does not declare OAuth, an external service,
or an application database. The MCP server writes task coordination state only
to the explicitly selected project's `.codex/cortex` directory.

## Data stored locally

The ledger can contain task objectives, gate state, delegation contracts,
sanitized worker reports, evidence summaries, questions, handoffs, file
manifests, bounded metrics, and privacy-limited lifecycle events. Secret-like
structured fields, bearer values, credential-bearing URIs, and environment
assignments are redacted before persistence. Reports and telemetry have
documented size and retention bounds.

Do not place credentials, private keys, personal data, customer exports, or
raw production logs in Cortex prompts, reports, fixtures, documentation, or
evidence. Repository releases exclude `.codex`, Python bytecode, symlinks, and
common secret-bearing filenames and credential-store paths. The archive check
uses only a committed `git archive HEAD`; until an initial commit exists, an
unborn `HEAD` prevents release-archive validation and publication.

## Upgrade backups

The installer may copy an authenticated retired Cortex profile, plugin cache,
or marketplace entry to `$CODEX_HOME/backups/cortex-upgrade/` before removal.
The Cortex backup directory is mode `0700`; each collision-safe backup slot and
its contents have group/world permissions removed. Backups remain on the local
machine, are never added to the release archive, and are retained or deleted
only by the operator. Cortex performs no automatic upload or retention purge.

## Operator control

Operators can inspect or delete a project ledger and upgrade backups using
their normal local filesystem controls. They are responsible for access
permissions, encrypted storage, retention requirements, and securely removing
sensitive local data. Host Codex, Git remotes, and repository platforms have
their own privacy terms and are outside this plugin's local storage contract.
