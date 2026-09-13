# Cortex development and release

Read repository AGENTS.md and affected project/feature docs. Preserve seven public
MCP tools, 23 profiles and the worker report protocol. Runtime belongs below
plugins/cortex. Quality metadata stays advisory, not routing or acceptance authority.

Use an isolated checkout preserving user changes. Run focused tests and the incident
regression evaluation. Regenerate the complete payload hash after final payload edits
before release-sensitive checks. Run package, source-only sync and full tests
sequentially on the same bytes. Reconcile release pins using actual observed hashes.

Prepare the isolated dev candidate only through cortex-dev. Never update the stable
plugin or host configuration. Live coordinators use gpt-5.6-luna/high; test workers use
Luna medium/high. Follow repository CLI-before-Desktop requirements and
[live qualification](live-qualification.md). Check process metrics as well as artifacts.
Report unrun checks and distinguish implementation, commit, push and acceptance.
