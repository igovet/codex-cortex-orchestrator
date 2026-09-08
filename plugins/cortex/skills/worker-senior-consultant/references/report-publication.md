# Report publication

Read this reference only when a report is ready to be drafted, published or
recovered.

Close every command or interactive session created by the assignment and retain its
terminal receipt before creating the draft. Use the live Cortex descriptions and
schemas for the selected report template, draft and writer operations.

Create one draft and treat its returned identifier, path, required first line and
Markdown as authoritative. Preserve the first line and following blank line. Edit
only that path with the native patch operation, replacing each returned guidance
marker with complete report content. The required first line is registration
metadata, not a guidance marker. Any marker check must target the actual returned
guidance markers and preserve that line. Retained patch content and a successful
patch receipt normally establish replacement without another shell read.

Before every draft or publication call, read its complete live declaration and
supply every required field, including on the initial call. Never probe required
fields with an empty argument object or infer defaults from another operation.

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

When the live schema calls for a delivery key, pass a literal UUID in the tool arguments.
Do not construct it with `crypto.randomUUID()` or another assumed JavaScript global
inside a `functions.exec` wrapper. Use the selected operation's initial-call and
retry contract; draft creation and report publication can require different keys.

Use the draft reader only after compaction, restart, an interrupted edit or a conflict
that makes retained content uncertain. Follow its bounded cursor until the affected
fragment is recovered. Correct deterministic edit or argument errors once from
observed state and the live declaration; do not create a competing draft.

A successful publication is the final tool action for the assignment. Return the new
report identifier and concise handoff through the native final response. Later
authorized continuation publishes a new immutable report that points to its
predecessor.
