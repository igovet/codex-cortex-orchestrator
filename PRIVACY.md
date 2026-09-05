# Privacy

Cortex stores original user requests, advisory governance and authored Markdown
locally. Documents live under the project's `.codex/cortex/<task>/`; the active
Codex home's `cortex/cortex.sqlite3` contains metadata and delivery receipts.
The server temporarily creates typed report and pipeline drafts in the project's
owner-private `.cortex/draft-reports/` and `.cortex/pipeline-drafts/` directories,
bound to the calling native thread. Agents fill only the returned file. A successful write removes its source
after the immutable task file and metadata commit; a failed write leaves it for an
exact retry or explicit retention cleanup.
No network service or telemetry endpoint is part of the runtime.

Optional local development observations contain operation outcomes and package
identity plus native thread/parent identifiers needed to verify task routing,
not report bodies, tool arguments or other transport metadata. Thread bindings
are retained locally with task metadata and deleted by task retention cleanup. The host and selected AI provider
may have their own data policies. Keep credentials and unnecessary personal data
out of report bodies, metadata and shared diagnostics. See [SECURITY.md](SECURITY.md).
