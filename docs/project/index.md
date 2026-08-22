# Project overview

<!-- GENERATED:START -->

## Purpose

Cortex 10.0.6 is an opt-in Codex plugin for staged multi-agent work. The
installable runtime is under [plugins/cortex](../../plugins/cortex/); root
scripts and tests support source development. New tasks use task contract
cortex/v10, orchestration lifecycle cortex/orchestration/v6, and SQLite schema
v15.

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

## Operational runbooks

- [SSH host troubleshooting](ssh-hetzner-troubleshooting.md) — read-only
  diagnosis for Codex, Python, plugin, and same-user cache prerequisites.
