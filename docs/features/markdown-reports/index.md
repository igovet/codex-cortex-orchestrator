# Markdown coordination

The coordinator keeps work order, dependencies, executors, model/effort choices
and intended checks in one task `pipeline.md`. Each complete new edition is
prepended, so the beginning always contains the freshest state and older editions
remain available through the same cursor reader.

The native host supplies thread and parent metadata. The server binds a coordinator
thread once and lets registered children inherit that task without model-authored
task identifiers. Every one of the 22 specialists receives a complete Agent v2
profile plus its concrete assignment and mandatory requirements.

The coordinator supplies exact short references for predecessor reports whose bodies
a worker needs. Workers read only those report pages; they do not list the catalogue
or read the current pipeline for routine startup. Catalogue and pipeline reads remain
available for a concrete missing fact or recovery after context loss. Coordinators do not read report
bodies, project files or project indexes; they delegate that evidence work and use
previews plus the current pipeline beginning. Each document page is at most 4,000
Unicode characters; agents follow its opaque cursor only for a concrete missing fact.

A worker requests a typed draft, receives its complete initial Markdown in the
`create_draft` result, confirms its short identifier in the returned path and marker,
and updates the file in place with complete English content. No immediate duplicate
read is needed; `read_draft` provides cursor-bounded recovery or a genuinely needed
later read of an existing draft. A coordinator uses the pipeline template for each new
edition and replaces its exact `{{CURRENT_...}}` placeholders in one in-place patch with independent
edit hunks. Publication sends only the short
draft identifier and compact metadata. Neither body nor path crosses MCP. The writer streams it into the immutable
`.codex/cortex/<task>/` report, preserves all bytes, commits only metadata to SQLite,
and deletes the draft afterward. Report size is limited by the filesystem and
available space rather than an MCP payload limit.

The actor edits the original server-created draft in place. Cortex records its device
and inode and rejects a deleted, replaced, renamed or recreated file before publication,
even when a replacement contains the expected visible marker.

Profiles share one generated publication protocol and retain their own specialist
evidence checklist. The profile catalogue fixes a default planning, investigation,
implementation, verification, documentation, or general draft class for every role;
the live audit rejects a different class. Optional examples cover planning,
investigation, implementation, verification, documentation and synthesis without
imposing a fixed report parser.

The coordinator owns adaptive delegation, steering and completion. It remains in a
native wait loop while work is unfinished and no real user question exists. Worker
questions arrive as concise handoffs; the coordinator presents detailed context,
choices and consequences in the user's language as ordinary chat text.

See [Markdown storage](../../project/storage.md) for the seven-operation runtime.

Every draft class rejects unchanged packaged guidance markers before publication.
Failed validation leaves the same draft editable. A process interruption between
pipeline rename and metadata commit restores the previously committed edition;
an exact subsequent publication adds the new edition once.
