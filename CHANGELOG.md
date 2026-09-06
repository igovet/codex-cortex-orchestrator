# Changelog

## 1.15.7

Restore explicit Codebase Memory discovery in all 22 worker profiles. Structural
code questions use the graph before filesystem symbol searches, with exact-workspace
index selection, relevant coverage checks and concrete source-fallback reasons.
Cortex-only tool discovery no longer implies that Codebase Memory is unavailable.
Delegations carry the exact worker-skill token and require complete skill loading
before tool discovery or project work. Discovery emits names before selected schemas.

Keep every worker progress update, question, blocker and verification message on
the native parent/subagent channel. Workers do not call app task-messaging tools
such as `codex_app.send_message_to_thread`; completed handoffs return natively.
The seven MCP operations and storage format remain unchanged.

## 1.15.6

Replaced the orchestration protocol with six storage operations. The coordinator
owns the pipeline, native agents, steering and completion. All 22 profiles use
one Markdown reporting protocol. Each task has one pipeline document with newest
editions first; ordinary reports are immutable files. Both the catalogue and
report bodies support bounded cursor reads. SQLite retains metadata and durable
publication intents. No compatibility protocol or orchestration hooks are shipped.
The server now creates typed project drafts, embeds each short draft ID in the
filename and Markdown, and binds it to the caller's native thread. The writer accepts
only that ID. Large reports and pipeline editions use bounded-memory publication
without an application total-size cap or executable-string encoding. The MCP contract now advertises input and
output schemas and provides safe English parameter corrections.
Editable drafts live under project `.cortex`, outside Codex's protected `.codex`
metadata directory; the MCP alone publishes final task files under `.codex/cortex`.
Tasks now bind to native host threads, including registered child/parent lineage.
Public tools contain no task selector or returned task identifier; pipeline reads
resolve automatically. Native profiles attach role instructions at spawn; named
skills load normally without copying their bodies into profiles or putting
installation paths in assignments. The coordinator preserves the user language
across progress messages and recovery, and owns pipeline/governance changes.
Its execution boundary explicitly forbids project reads, shell, build, test,
package, browser and edit operations except filling its returned pipeline draft;
all such work is delegated to native profiles.
Report field guidance treats Markdown as literal data in executable host wrappers.
Catalogue previews target half their enforced limit to leave writing margin.
Only the content-addressed cache suffix changes for this replacement.
