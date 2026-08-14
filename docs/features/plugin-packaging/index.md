# Plugin packaging and marketplace validation

<!-- GENERATED:START -->
## Purpose

This feature packages Cortex as a repository-local Codex plugin and validates that marketplace metadata, plugin companions, profiles, and skills agree.

## Key files and dependencies

- [plugin.json](../../../plugins/cortex/.codex-plugin/plugin.json) declares the `cortex` plugin, version, skills directory, and MCP companion.
- [.mcp.json](../../../plugins/cortex/.mcp.json) exposes the Python MCP server.
- [marketplace.json](../../../.agents/plugins/marketplace.json) is the sole repository-managed root marketplace entry; it points only to `./plugins/cortex` and is not a personal marketplace.
- [validate-cortex-marketplace.py](../../../scripts/validate-cortex-marketplace.py) validates the root-only marketplace and plugin contract.
- [sync-cortex.sh](../../../scripts/sync-cortex.sh) validates sources, registers the local marketplace, supports a read-only `--check`, and offers a no-write `--dry-run` preview.
- [probe-fresh-cortex-plugin.py](../../../scripts/probe-fresh-cortex-plugin.py) tests fresh isolated CLI registration when the Codex CLI is available.
- [profiles.json](../../../plugins/cortex/profiles.json) is the canonical profile, routing, and generated-catalog contract validated against the bundled TOML files and orchestration skill.

## Behavior and status

The validator requires one root marketplace plugin entry, a canonical regular plugin directory, a version-aligned manifest/server, 21 registered profile files with structured playbooks, 13 validated gate briefings, and the ten expected skills. For every profile it validates exact TOML identity, description, and sandbox parity plus complete automatic/manual route metadata, gates, and selection/avoidance guidance. The ordered implementation contract must route each of the eight specialist writers exactly once before the `general` fallback, and the generated root-skill catalog must exactly match `profiles.json`. It rejects nested marketplace artifacts, symlinks, and plugin-local `.codex/` runtime state. The installer re-registers the local plugin with a remove/add cycle; when present, it captures and restores the user's Cortex MCP `default_tools_approval_mode` override across that cycle and atomically enforces `[agents] default_subagent_model = "gpt-5.6-luna"`. A different default is privately backed up before replacement; comments, unrelated keys, and mode are preserved. Its `--check` mode is read-only and detects installed-content drift, missing or non-Luna global routing configuration, or managed legacy artifacts, while `--dry-run` reports the planned changes without writing. The fresh probe uses a copy of the root checkout with temporary `HOME` and `CODEX_HOME`; it prints `SKIP` if the CLI is absent. The tracked-release archive validator is blocking in CI and rejects runtime state, secret-prone paths, missing release policies, private local home paths in public documents, and unsafe archive entries. It validates only committed `HEAD`; the package contract requires the exact five-tool v3 public surface—the three coordinator lifecycle operations plus worker `record_report` and coordinator `read_worker_report`—and aligned plugin/server versions. Optional public manifest metadata remains unchanged until its exact installed Codex schema is verified.

## Verification

Use the marketplace validator, plugin probe, and installer check listed in [verification.md](../../project/verification.md). The current candidate is installed and content-verified as `3.2.1+codex.20260814203024`; marketplace validation, installer check/dry-run, the isolated fresh-plugin probe, 220 tests, cold boot, compilation, and shell syntax pass. Tracked release remains correctly blocked by the older committed `HEAD`, and publication is not verified.
<!-- GENERATED:END -->
