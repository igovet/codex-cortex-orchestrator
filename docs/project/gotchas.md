# Gotchas

The current host task may still expose an older installed plugin catalogue.
Source changes do not replace that catalogue. Test the isolated candidate in a
new ordinary Codex session; never update the stable plugin implicitly.

Pipeline updates expire its document cursors. Start at the beginning again.
Catalogue snapshots retain earlier metadata, while a report read describes the
current document. Keep SQLite and project task artifacts together in backups.

Draft paths and file identities are server-owned. Create a typed draft first, edit
only its returned file in place, keep the short identifier marker, and publish it from the same
native thread. A coordinator can safely hold several pipeline or report drafts
because each has a distinct short ID.
Never delete and recreate a draft: publication verifies its original device and inode.

After summarization, reread current context instead of relying on remembered
summaries. Missing indexes justify bounded worker discovery, not automatic harvest.
Retention cleanup must receive active task exclusions from the coordinator;
storage cannot infer native agent liveness.

A plugin cache containing agent TOML files does not populate Codex's personal
agent registry. Cortex therefore distributes all 22 specialist profiles as worker
skills and assigns their exact tokens to ordinary native subagents. Dev preparation
must not register personal profiles, or it can hide marketplace installation bugs.
Verify runs with an empty personal agents directory and inspect actual skill loading.
