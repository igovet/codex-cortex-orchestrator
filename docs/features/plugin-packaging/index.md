# Plugin packaging and validation

<!-- GENERATED:START -->

## Purpose

Cortex 10.0.5 is packaged as a repository-local Codex plugin. Marketplace
metadata, the MCP server, profiles, skills, hooks, schemas, and validators must
describe the same fresh v10 contract.

## Package files

- [plugin.json](../../../plugins/cortex/.codex-plugin/plugin.json) declares version 10.0.5 and bundled components.
- [.mcp.json](../../../plugins/cortex/.mcp.json) launches the Python MCP server.
- [marketplace.json](../../../.agents/plugins/marketplace.json) is the repository marketplace entry.
- [validate-cortex-marketplace.py](../../../scripts/validate-cortex-marketplace.py) validates package structure.
- [sync-cortex.sh](../../../scripts/sync-cortex.sh) provides check and dry-run validation.
- [cortex-host-preflight.py](../../../scripts/cortex-host-preflight.py) performs read-only host diagnostics.
- [profiles.json](../../../plugins/cortex/profiles.json) is the canonical profile and routing source.

## Current contract

The package exposes exactly nine operations. Coordinator tasks expose
`start_orchestration`, `continue_orchestration`, `manage_orchestration`,
`manage_governance`, and `read_worker_result`. Worker tasks expose
`worker_question`, `record_attempt_event`, `complete_attempt`,
`read_dispatch_briefing`, and `read_worker_result`.

Workers send bounded semantic events and one `AttemptResult`. The server owns
identity, timestamps, dispatch/profile/phase, task revision, changed files,
checks, verification observations, and result links. `ContextCompiler` and
`HandoffCompiler` expose only bounded, target-specific context. Result views
are rebuildable and cannot authorize lifecycle transitions.

The validator checks the v10 registry, schema v15, strict coordinator and
worker projections, profile TOML parity, prompt contract v3, the six hooks,
launcher paths, and the knowledge-route inventory. The prompt contract has one
stable v3 renderer and no alternate prompt format or comparison runner.

All package commands are source-mode checks unless explicitly documented as
installation checks. They do not claim that a user's installed cache is
updated or trusted.

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
