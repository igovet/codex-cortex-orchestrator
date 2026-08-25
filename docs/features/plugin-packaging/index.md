# Plugin packaging and validation

<!-- GENERATED:START -->

## Purpose

Cortex 11.0.1 is packaged as a repository-local Codex plugin. Marketplace
metadata, the MCP server, profiles, skills, hooks, schemas, and validators must
describe the same v11 task and capability contract.

## Package files

- [plugin.json](../../../plugins/cortex/.codex-plugin/plugin.json) carries the release version and bundled components; the v11 release label is 11.0.1.
- [.mcp.json](../../../plugins/cortex/.mcp.json) launches the Python MCP server.
- [marketplace.json](../../../.agents/plugins/marketplace.json) is the repository marketplace entry.
- [validate-cortex-marketplace.py](../../../scripts/validate-cortex-marketplace.py) validates package structure.
- [sync-cortex.sh](../../../scripts/sync-cortex.sh) provides check and dry-run validation.
- [cortex-host-preflight.py](../../../scripts/cortex-host-preflight.py) performs read-only host diagnostics.
- [profiles.json](../../../plugins/cortex/profiles.json) is the canonical profile and routing source.

## Current contract

The package exposes one MCP tool for each semantic action. Lifecycle,
inspection, recovery, interaction, approval, artifacts, lane/resource control,
governance, attempt submission/repair, and scoped reads are separate tools,
with no multiplexed selector or aliases. `tools/list` is the authoritative
operation inventory.

`public_contracts.py` supplies every tool's complete closed one-level
`inputSchema`; the runtime validator consumes that same schema. Descriptions
remain short and semantic. Skills and prompts contain no copied argument names,
field constraints, or schema templates.

Workers send complete semantic events and one `AttemptResult`. The server owns
identity, timestamps, dispatch/profile/phase, task revision, changed files,
checks, verification observations, and result links. `ContextCompiler` and
`HandoffCompiler` expose complete, target-specific context. Result views
are rebuildable and cannot authorize lifecycle transitions.

The validator checks the v11 action-specific catalog, schema v18, strict coordinator and
worker projections, profile TOML parity, Prompt Contract v3, the five hooks,
launcher paths, and the knowledge-route inventory. The prompt contract has one
stable v3 renderer and no alternate prompt format or comparison runner.

All package commands are source-mode checks unless explicitly documented as
installation checks. They do not claim that a user's installed cache is
updated or trusted.

The v11 release label is 11.0.1. The package must not expose `create_thread`,
session/environment authorization, server-owned CLI or executor launches,
`repair_planning`, or manually authored `advance`/`completions`. The only
worker lifecycle is the exact server-issued `spawn_agent` target, exact `wait`,
an action-specific canonical wave read, and server-derived continuation. Only
the exact signed V11 v1--v8 lineage upgrades atomically to schema v18; unknown
histories fail closed. The SQLite schema history and independent Prompt
Contract/question schema histories remain storage/documentation history, not
alternate public task protocols.

## Security-sensitive packaging rules

The launcher requires Python 3.11+ and resolves the bundled runtime. Hooks
must invoke the same installed cache's launcher and hook script. Public task
references are opaque and exact; APIs never select tasks by directory scan.
Malformed input returns bounded validation diagnostics. Secrets, credentials,
personal data, and private task contents must not enter prompts, generated
views, issues, or logs.

## Verification

Run the checks listed in [verification.md](../../project/verification.md).
Use `sync-cortex.sh --dry-run` for a no-write package preview and the
marketplace validator for the repository package. Source and tests are
authoritative if this generated page drifts.

<!-- GENERATED:END -->
