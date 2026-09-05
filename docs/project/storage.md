# Markdown storage

Each task has a server-generated short internal identifier and one canonical
project root. SQLite is an index and recovery journal. It stores task bindings,
report metadata, digests, governance choices and delivery receipts, never report
bodies. Its default location is `$CODEX_HOME/cortex/cortex.sqlite3`; `CODEX_HOME`
already names the `.codex` directory.

Editable drafts and published task documents use separate private project roots:

```text
<project_root>/.cortex/
├── draft-reports/
│   └── <short-draft-id>.md
└── pipeline-drafts/
    └── <short-draft-id>.md

<project_root>/.codex/cortex/
└── <short-task-id>/
    ├── pipeline.md
    └── <short-report-id>.md
```

Codex reserves the project `.codex` directory against ordinary agent writes.
Drafts therefore live under `.cortex`, where native file tools can edit them. Only
the MCP server writes published documents below `.codex/cortex`.

The original request and governance choices are ordinary immutable reports. Before
authored publication, `create_draft` allocates a short identifier, writes a matching
identifier marker and selected heading template, and stores its task, calling thread,
kind and exact path in SQLite. Report drafts go under `draft-reports`; pipeline
editions go under `pipeline-drafts`. One native thread may hold multiple drafts.
The row also stores the file's original device and inode. `create_draft` returns the
complete initial Markdown, so the actor updates only the returned file in place without
an immediate duplicate read. The bounded, same-thread `read_draft` operation is reserved
for recovery or a genuinely needed later read; deletion, replacement, renaming or recreation is rejected
with an actionable `draft_replaced` error. The writer accepts the short identifier,
requires the same task and native thread, canonicalizes the stored path, rejects
directories, links and special files, validates UTF-8 while reading fixed-size
blocks, and computes byte size and SHA-256.

Publication writes a private temporary file in the destination task directory,
flushes and fsyncs it, atomically renames it to the short report filename, commits
only metadata and relationships in SQLite, and then removes the project draft.
An error before commit leaves the draft. A database failure rolls back the newly
published destination. A partially written temporary file never becomes a report.
Content remains byte-for-byte unchanged; there is no newline normalization,
truncation, summarization, or application-level report-size limit.

Each logical draft creation and publication has a caller delivery key. An exact
publication retry returns its original receipt even after the source draft was removed. If the same path is recreated,
its SHA-256 must equal the accepted report before the receipt is replayed and the
duplicate draft is removed. Changed arguments or bytes under the same key produce
an explicit conflict. Changed content uses a newly allocated draft and key.

The task has exactly one `pipeline.md`. Pipeline text does not pass through MCP or
`draft-reports`; the coordinator creates a pipeline draft, replaces every exact
`{{CURRENT_...}}` placeholder in its returned file,
and publishes the short identifier. The writer prepends those bytes to the same file with a Markdown separator and keeps
older editions below. The pipeline report identifier stays stable.

The catalogue returns one compact metadata entry per report, newest activity first,
using a stable high-water snapshot across cursor pages. Document reads return
bounded Unicode slices. Cursor tokens are compact and contain a short non-reversible
task binding, document reference, digest prefix and position so agents can copy them
without carrying task identifiers. Ordinary report cursors survive restart. A pipeline update
invalidates its old cursor, so recovery restarts from the newest beginning.

`clear N days` is a host-side retention command rather than an MCP tool. It
selects this project's tasks by latest recorded activity, protects tasks linked to
supplied active native threads, and removes their SQLite rows and exact task
directories. It removes only server-created drafts whose database row makes their
task relationship unambiguous; published drafts also require their recorded digest.
It never deletes shared draft directories or unknown files.

Short task, report and draft identifiers use a type prefix plus 12 hexadecimal characters,
with collision checks inside serialized transactions. They never appear as task
selectors in MCP arguments or results. Cursor bindings use a non-reversible task
fingerprint rather than embedding that identifier. Host-supplied thread and parent metadata
resolves the task. Missing, unregistered or conflicting ancestry fails explicitly;
there is no latest-task fallback.

Storage format 10 accepts no earlier layout and provides no compatibility reader or
automatic migration. Back up the SQLite index and project task directories together
while access is stopped.

Publication recovery preserves pipeline backups until the committed digest has
been restored or confirmed. A destination rename is registered for rollback before
its directory sync, so a sync failure cannot strand an uncommitted edition.
All packaged template markers, including ordinary report guidance, must be filled
before publication; rejection preserves the editable draft and accepts no receipt.
