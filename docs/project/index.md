# Project overview

<!-- GENERATED:START -->

## Purpose

Cortex 11.0.1 is an opt-in Codex plugin for staged multi-agent work. The
installable runtime is under [plugins/cortex](../../plugins/cortex/); root
scripts and tests support source development. New tasks use task contract
v11 and SQLite schema v19. The coordinator privately carries its task
authority; a worker carries only its exact native dispatch authority.
`start_orchestration` is the sole task creator and initial coordinator
capability issuer. Native worker execution is only the exact
native V2 `spawn_agent` → generic timeout-bounded `wait_agent` cycles →
canonical terminal results plus exact matching terminal Stops → action-specific
canonical wave read and server-derived continuation route. Host MCP
Host-owned identity binding joins the authorized call to its exact native child;
unknown or disabled hook state fails closed. Cortex owns and issues every opaque ref;
models copy them byte-for-byte and never infer one from a session, host,
thread, or path.
After a completed wave is read, use `revise_future_pipeline` only for
unexecuted future work and `append_rework_wave` only for product correction of
a completed canonical result. Technical failure uses server-owned
Luna-to-Terra-to-Sol replacement with capability-safe profile resolution.
Every returned worker repeats the native lifecycle, and required governance
closure executes before final handoff.

## Stack and entry points

- Python 3.11+ standard library: [cortex.py](../../plugins/cortex/scripts/cortex.py), launched by [cortex-launcher](../../plugins/cortex/scripts/cortex-launcher).
- Bash: [sync-cortex.sh](../../scripts/sync-cortex.sh) validates and previews the package.
- unittest: regression coverage is in [tests](../../tests/).
- Lifecycle hooks: [hooks.json](../../plugins/cortex/hooks/hooks.json) and [cortex_hook.py](../../plugins/cortex/scripts/cortex_hook.py).
- Plugin metadata: [plugin.json](../../plugins/cortex/.codex-plugin/plugin.json) and [.mcp.json](../../plugins/cortex/.mcp.json).

## Runtime requirements

The server and validators require Python 3.11+ with tomllib. Set CORTEX_PYTHON
to an absolute executable when python3 is not the desired runtime. Installation
also requires Bash 3.2+, Git, and Codex. The launcher works with the stock
macOS shell.

## Feature registry

- [Orchestration ledger](../features/orchestration-ledger/index.md)
- [Lifecycle telemetry](../features/lifecycle-telemetry/index.md)
- [Plugin packaging](../features/plugin-packaging/index.md)
- [Release readiness](../release-readiness.md)
- [Knowledge-route contract](../features/knowledge-route-contract/index.md)

<!-- GENERATED:END -->

## Architecture decisions

- [Storage classification](storage-classification.md) — authority, retention,
  capability briefings, and rebuildable views.
