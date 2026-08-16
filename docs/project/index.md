# Project overview

<!-- GENERATED:START -->
## Purpose

Cortex is a repository-source Codex plugin that provides an opt-in, durable `cortex/v8` control plane with public lifecycle schema `cortex/orchestration/v4` for staged agent work. Legacy v7/v3 ledgers are unsupported and are not migrated or resumed. Its installable runtime is contained in [plugins/cortex](../../plugins/cortex/); root-level scripts and tests support its development.

## Stack and entry points

- Python 3 standard library: the MCP server is [cortex.py](../../plugins/cortex/scripts/cortex.py), configured by [.mcp.json](../../plugins/cortex/.mcp.json).
- Bash: [sync-cortex.sh](../../scripts/sync-cortex.sh) installs or checks the repo-local plugin.
- `unittest`: regression coverage is in [tests](../../tests/).
- Lifecycle hook configuration is [hooks.json](../../plugins/cortex/hooks/hooks.json), which invokes [cortex_hook.py](../../plugins/cortex/scripts/cortex_hook.py).

## Directory map

- [plugins/cortex](../../plugins/cortex/): plugin manifest, MCP server, profiles, skills, and hooks.
- [scripts](../../scripts/): marketplace validation, installation, cold-boot smoke test, and isolated plugin probe.
- [tests](../../tests/): control-plane and invariant regressions.
- [.agents/plugins/marketplace.json](../../.agents/plugins/marketplace.json): root marketplace metadata; nested marketplace directories are rejected.

## Runtime requirements

The server and repository validators require `python3`; installation also requires Bash, Git, and the Codex CLI. The isolated plugin probe reports `SKIP` when the CLI is unavailable; it is not registration evidence.

## Feature registry

- [Orchestration ledger, report bus, and lane lifecycle](../features/orchestration-ledger/index.md)
- [Lifecycle telemetry hooks](../features/lifecycle-telemetry/index.md)
- [Plugin packaging and marketplace validation](../features/plugin-packaging/index.md)
- [Release readiness](../release-readiness.md)
- [Knowledge-route contract](../features/knowledge-route-contract/index.md)
<!-- GENERATED:END -->
