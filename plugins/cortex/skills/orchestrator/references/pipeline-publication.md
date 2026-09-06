# Pipeline publication and recovery

Read this reference only when creating, editing, publishing or recovering the
pipeline draft.

Use the live Cortex descriptions and schemas. Create one pipeline draft for the
current edition and treat the returned Markdown, draft identifier, path and required
first line as authoritative. Preserve the first line and following blank line.
Replace each current-edition guidance marker once with concise English content; do
not rewrite, rename, move or delete the draft.

Edit only the exact pipeline draft path returned to this coordinator. Pass the exact
patch intact to the native patch tool, directly or through a safe host wrapper, and
inspect its complete receipt. An inert JavaScript string form is valid; executable
interpolation or substitution of draft content is not. Never pass the Markdown body
through a Cortex writer or shell command. Publish the draft once with short metadata
through the live writer and retain its receipt. An acknowledged publication is
immutable and must not be replayed.

Use the draft reader only after compaction, restart, interrupted editing or a
conflict that makes the retained Markdown uncertain. Follow its bounded cursor until
the exact affected fragment is recovered. Correct a deterministic error once from
the live declaration and observed draft state. For uncertain publication, follow the
writer's advertised retry guidance; do not create a competing draft.

The current edition must preserve active and cancelled requirements, decisions,
assignments, dependencies, resource owners, open actions and provenance pointers.
Never remove unresolved work merely to make the pipeline appear complete.
