# Markdown coordination

The coordinator keeps work order, dependencies, executors, model/effort choices
and intended checks in one task `pipeline.md`. Each complete new edition is
prepended, so the beginning always contains the freshest state and older editions
remain available through the same cursor reader.

The native host supplies thread and parent metadata. The server binds a coordinator
thread once and lets registered children inherit that task without model-authored
task identifiers. Every one of the 22 specialists loads its complete packaged worker skill alongside its concrete assignment and mandatory requirements.

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

The original-request report comes from the host's typed user-message receipt for
the current native turn. Cortex resolves that source itself within the current
thread and canonical project; the coordinator does not retype it. Explicit literal
credential redactions are applied before immutable publication. Missing host source
fails closed, and the creation receipt exposes its digest without returning text.


Native steering is archived on the next successful coordinator Cortex operation,
including catalogue reads. Each typed native user message becomes a separate
immutable report in its original order; the pipeline remains a model-authored
working summary. Capture is not an idle background service and cannot prove that
the model correctly applied every requirement. Text is preserved without trimming,
translation or summarization, except explicitly requested literal credential
redactions. Attachments are not copied by this text-source reader.

The private host-file cursor and native message identities commit atomically with
report metadata and the requested operation. Retries, restart and repeated native
receipts do not duplicate messages. Failed operations retain the previous cursor;
changed files, conflicting message identities or unavailable source fail closed.
Workers never read or capture another thread's native input. Source cursor and
identity metadata are removed with task retention. Existing tasks without a source
cursor begin with the current native turn; earlier unarchived steering is not
retroactively guaranteed.
