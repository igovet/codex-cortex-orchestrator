# Privacy policy

Cortex is a local Codex plugin. It declares no OAuth flow or external Cortex
service. V12 writes canonical coordination data only below the current user's
private Codex state directory:

```text
~/.codex/cortex/v12/projects/p-<sha256-of-resolved-project-root>/
├── cortex.db
└── tasks/<task-id>/
```

`cortex.db` is the sole authority. The task subtree contains disposable
human-readable Markdown views derived from that database. Cortex never creates
a `.codex` directory, database, plan, report, decision, timeline, view, or
ignore entry under the selected `project_root`.

## Data stored locally

The ledger can contain the English-normalized task objective and result
contract, exact original user request and asserted language, delegation
instructions, worker reports and report chunks, plan reports, coordinator-
attributed user decisions, governance assessments and closures, initiatives
and links, ordered timeline events, idempotency metadata, and projection-job
metadata. Reports and arbitrary JSON context have explicit size limits, but
valid content is not automatically classified or redacted before persistence.

Internal agent communication and durable operational records are English.
Exact user-authored text is retained only in labeled fields such as
`user_request_original` and `response_original`, beside separate English
normalizations. Generated Markdown uses English operational headings and
renders arbitrary values as inert labeled content. Coordinator summaries and
verified view links follow the user's language, but localization does not
replace the canonical English/original fields.

Do not place credentials, private keys, personal data, customer exports, raw
production logs, or unnecessarily sensitive implementation details in task
context, delegations, reports, plans, decisions, governance records, fixtures,
documentation, or release evidence. Report IDs should be handed off instead of
copying report bodies into prompts. Tool errors and diagnostics are bounded and
sanitized; any private tool-error log remains same-user sensitive data and must
never be pasted into chat, issues, commits, or external systems.

Repository release candidates exclude `.codex`, Python bytecode, symlinks,
common secret-bearing filenames, credential-store paths, and runtime V12 data.
Source validation does not inspect or publish a user's ledger.

## Filesystem and view privacy

V12 state, project-shard, task, and view directories use mode `0700`.
Database, WAL, SHM, and generated Markdown files use `0600`. Database and view
paths are rejected when their required file type or no-symlink boundary is not
met.

Canonical mutations commit before best-effort view materialization. A view is
returned with an absolute path only when its `human_view` status is `ready` and
the runtime has verified task-subtree containment, regular-file type, digest,
and current source sequence. A stale, conflicted, unavailable, or disabled view
has no publishable path. Directly changed generated files are preserved as
conflicts rather than overwritten. The coordinator must pair every ready link
with a localized summary; otherwise it discloses the limitation and summarizes
canonical evidence inline.

Markdown is never parsed back into SQLite and is not worker input, a bearer
capability, a receipt, approval, or recovery source. Deleting a derived view
does not delete canonical data; a later inspection may regenerate it.

## V12 maintenance backups

The optional local administrator CLI can create a sealed backup of the complete
project shard, anchored to one task ID, below:

```text
~/.codex/cortex/v12/projects/p-<hash>/backups/<task-id>/<backup-id>/
```

Each bundle contains an owner-only SQLite copy and bounded manifest with shard,
anchor-task, affected-task-count, size, time, and SHA-256 metadata. It is local
private data and may contain every task, report, plan, decision, and governance
record in that shard—not merely the anchor task. SQLite may also leave
owner-private WAL/SHM support files in the same fixed bundle; retention treats
them as sensitive bundle members and accepts no other extras. The CLI accepts
no arbitrary export path and never uploads a backup.

There is no automatic backup purge. Retention is explicit, dry-run by default,
and can remove only named complete manifest-bound maintenance bundles after
validation; it never prunes the canonical database. Restore is offline only and
requires the operator to stop all normal Cortex MCP access first. Operators are
responsible for encrypted backup storage, retention, access control, and secure
deletion.

## Upgrade backups

Repository development/update tooling may copy a retired Cortex profile,
plugin cache, or marketplace entry to `$CODEX_HOME/backups/cortex-upgrade/`
before removal.
The Cortex backup directory is mode `0700`; each collision-safe backup slot and
its contents have group/world permissions removed. Backups remain on the local
machine, are never added to the release archive, and are retained or deleted
only by the operator. Cortex performs no automatic upload or retention purge.

## Operator control

Operators can inspect or delete a host-private project ledger, its derived
views, and upgrade backups using normal local filesystem controls. Cortex does
not automatically purge task ledgers or derived views. Operators are
responsible for encrypted storage, retention requirements, backups, and secure
removal of sensitive local data.

V12 never opens, imports, migrates, deletes, or modifies a V11 database or
legacy project-local V11 tree. V11 tools and unfinished tasks are incompatible
with V12. Host Codex, native workers, external tools, Git remotes, repository
platforms, and any separately installed MCP server have their own privacy
boundaries and terms outside this local Cortex storage contract.
