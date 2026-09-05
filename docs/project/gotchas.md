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

Native profiles must be registered in a Codex agents directory before starting
a task. Plugin-cache TOML files alone are not native profile registration. The
isolated candidate setup registers all 22 without modifying stable agents. The
coordinator selects the profile through the advertised spawn contract; workers
never initialize themselves by reading TOML or plugin source.

Ordinary plugin installation must run the packaged `scripts/cortex_setup.py --install`
and then its read-only check. Dev preparation now uses this identical program.
A host with zero registered profiles can use all Cortex MCP tools yet stop before
its first worker. Check the native spawn catalogue after restarting Codex; testing
only MCP availability does not establish a working installation.
