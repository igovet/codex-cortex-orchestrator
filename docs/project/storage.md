# Markdown storage

Each task has one canonical project and native coordinator binding. Children inherit
that task only through verified native parent metadata. There is no latest-task
fallback or model-supplied task selector. Binding receipts expose the selected
Cortex/normal state. Receipts establish local routing, not authentication against
a malicious same-user process.

SQLite lives under `$CODEX_HOME/cortex/cortex.sqlite3` by default. It stores metadata,
relationships, delivery receipts, source revisions and recovery information. Body
text remains in real Markdown:

```text
<project>/.cortex/draft-reports/<draft>.md
<project>/.cortex/pipeline-drafts/<draft>.md
<project>/.codex/cortex/<task>/pipeline.md
<project>/.codex/cortex/<task>/<report>.md
```

Codex protects ordinary agent writes to project `.codex`, so editable drafts use
`.cortex`. Only storage publication writes final task documents. Keep these private
files out of repository documents and diagnostics.

## Publication and recovery

The draft creator allocates a short identifier, canonical path and required marker,
and records its task, native owner, device and inode. Actors edit that exact file
in place. Replacement, deletion, renaming, recreation, symlinks, special files and
unsafe ownership are rejected. Unfilled packaged guidance markers also prevent
publication; this is template validation, not a semantic acceptance test.

Publication streams complete UTF-8 in fixed blocks, computes size and SHA-256,
flushes and fsyncs a private temporary file, atomically renames it, commits metadata,
and removes the draft. Bytes are not normalized, summarized or truncated. There is
no total report-size cap beyond the filesystem and available space. Failures before
commit preserve the draft and roll back uncommitted publications. Pipeline backups
retain the committed version until restoration or digest confirmation.

There is one pipeline per task. A new complete edition is prepended with a separator,
leaving older editions below. Ordinary reports are immutable. A publication delivery
key replays its original accepted receipt; changed arguments or recreated changed
bytes conflict. Replay does not repeat later state transitions.

Recovery runs for the resolved task only. It never traverses every task before a
normal operation. A corrupt neighboring pipeline does not prevent another catalogue
or report read. Retention and publication recovery use exact registered relationships
and preserve unknown files and shared directories.

## Bounded reads and checked identities

The newest-first catalogue uses a stable high-water snapshot across bounded cursor
pages. Report and draft reads use Unicode slices of at most 4,000 characters per
page, with cursors allowing access to the remaining document. Pipeline updates expire
its older document cursors; restart from the newest beginning. Ordinary immutable
report cursors survive process restart.

Each MCP process caches at most 128 validated file identities and at most 1,024 sparse
byte offsets per document. Unchanged files avoid complete digest rereads and repeated
prefix decoding. Identity changes trigger full integrity validation. The reader
checks identity around page access and does not retain whole report bodies in cache.
These checks are integrity detection, not a filesystem sandbox.

## Sources and provenance

Task creation obtains exact typed user messages from the current native Codex turn.
The reader uses the active home's explicitly validated `state_5.sqlite` format,
matched canonical project and owned regular session file. The first session metadata
must match the native thread and project. It never guesses a different index or a
foreign session. Blockwise boundary search and forward streaming replace the fixed
8 MiB tail; oversized records proven irrelevant to source selection can be skipped.
Unknown or oversized source records remain explicit gaps.

Relevant event/session JSONL records are limited to 4 MiB; one capture batch is
limited to 1,000,000 text characters and 1,024 native messages to protect memory. These are source-capture limits, not report pagination limits; a capture
failure leaves existing reports available with explicit completeness. Distinct native
message IDs produce distinct source reports even for identical text. The original
text retains its whitespace and language, except explicitly requested literal
credential redactions.

Attachments retain an available file/resource and a recovery method, or an explicit
unavailable entry. A reference is not proof that content was copied or inspected.
The pipeline separately records the model's interpretation, cancelled conditions,
assignments, owners and unfinished actions.

Every authored report records the source revision and checked artifact versions
underlying it. The default source revision is the draft's creation revision, rather
than silently adopting newer requirements at publication. Artifact versions are
reported evidence, not automatically executed checks. New sources and observed file
changes signal reconciliation; the coordinator decides whether prior checks remain
valid. Bounded catalogue discovery also exposes changes with allowlisted hook-observation
metadata (known status, exit/session receipts, truncation, actor scope and changed
paths), and the caller's unfinished
drafts, each with its own continuation mechanism.

Host-source errors do not deny archived report reads. Operation receipts expose
complete, partial, unavailable or unattempted capture as applicable. Native source
capture and metadata commit atomically with the operation; exact retries do not
collapse distinct native events. `normal` suspends capture and later reactivation
must skip the inactive interval rather than backfill ordinary conversations.
Ambiguous boundaries remain explicit gaps.

Hooks share the same services. A documented `UserPromptSubmit` without a unique
native message receipt records deferred capture, never a guessed identity or raw
publication that bypasses a later credential redaction. See [hook coverage](../features/lifecycle-hooks/index.md).

## Offline format 10→11 migration

Runtime accepts format 11 only. There is no automatic migration or compatibility
reader. Stop all CLI, Desktop, MCP and hook access, and back up every affected
project's task/draft directories. Then run the explicit metadata conversion:

```bash
python3 -B plugins/cortex/scripts/cortex_migrate.py --storage-dir /absolute/private/store --backup /absolute/private/cortex-v10.sqlite3 --access-stopped
```

The backup path must be new. The migrator takes an exclusive cooperative access lock
and database lock, creates and verifies the SQLite backup, adds metadata, initializes
known source order and marks unknown historical provenance explicitly. It never
opens or rewrites Markdown. Runtime connections hold shared access locks. Older
clients must still be stopped; an advisory lock cannot stop an uncooperative process.

Keep SQLite and project directories together for restoration. Failed migration must
not be treated as permission to start normal access without checking the receipt.
Retention removes source and hook metadata with the selected task. See
[security](../../SECURITY.md) and [verification](verification.md).
