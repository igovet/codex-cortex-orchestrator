# Report publication

Read this reference only when a report is ready to be drafted, published or
recovered.

Close every command or interactive session created by the assignment and retain its
terminal receipt before creating the draft. Use the live Cortex descriptions and
schemas for the selected report template, draft and writer operations.

Create one draft and treat its returned identifier, path, required first line and
Markdown as authoritative. Preserve the first line and following blank line. Edit
only that path with the native patch operation, replacing each returned guidance
marker with complete report content.

The initial `create_draft` call must include the required `template` argument from
the live schema; use `template: "general"` for an ordinary worker report. Never
call it with an empty argument object or infer a default template.

Pass the exact patch intact to the native patch tool, directly or through a safe host
wrapper. An inert JavaScript string or `String.raw` template without substitutions
is valid. Never construct draft content through executable interpolation, command
substitution or shell evaluation, and never send the Markdown body through a Cortex
writer. Do not rewrite, rename, move or delete the draft. Build the patch from the
authoritative current marker text; on a context conflict, reread and correct once.


Publish once with concise metadata and inspect the full receipt. The server owns the
atomic publication and draft cleanup. Keep an acknowledged report identifier and
never replay its mutation to confirm success. If delivery is uncertain, follow the
writer's live retry contract.

When a retry requires `request_key`, pass a literal UUID in the tool arguments. Do
not construct it with `crypto.randomUUID()` or another assumed JavaScript global
inside a `functions.exec` wrapper: that runtime may not expose `crypto`, and the
wrapper can fail before Cortex receives the publication call. Omit `request_key` for
the initial publication when the live contract permits it; use a literal retained
key only for the exact retry required by the writer contract.

Use the draft reader only after compaction, restart, an interrupted edit or a conflict
that makes retained content uncertain. Follow its bounded cursor until the affected
fragment is recovered. Correct deterministic edit or argument errors once from
observed state and the live declaration; do not create a competing draft.

A successful publication is the final tool action for the assignment. Return the new
report identifier and concise handoff through the native final response. Later
authorized continuation publishes a new immutable report that points to its
predecessor.
