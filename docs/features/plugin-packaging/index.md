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
- [profiles.json](../../../plugins/cortex/profiles.json) is the canonical profile, adaptive model/effort routing, implementation routing, and generated-catalog contract validated against the bundled TOML files and orchestration skill.
- [feature-census.md](../../../plugins/cortex/skills/knowledge-harvest/references/feature-census.md) defines the bundled exhaustive inventory, coverage-matrix, feature-page, and harvest failure contract.

## Behavior and status

The current source manifest and server declare plugin version `6.1.3`; the
local registration is installed and content-verified as
`6.1.3+codex.<build>`.

The validator requires one root marketplace plugin entry, a canonical regular plugin directory, a version-aligned manifest/server, 21 registered profile files with structured playbooks, 13 validated gate briefings, and the ten expected skills. For every profile it validates exact TOML identity, description, sandbox parity, and the machine-validated execution contract. The tracked-release archive validator is blocking in CI and rejects runtime state and unsafe archive entries. It validates only committed `HEAD`; the package contract requires the exact seven-tool v4 public surface—the three coordinator lifecycle operations plus worker `worker_question`/`record_report`, identity/digest-scoped `read_dispatch_briefing`, and scoped predecessor `read_worker_report`—and aligned plugin/server versions. A briefing read can return only the exact active dispatch artifact, and a successor report read is accepted only for explicitly supplied predecessor refs with exact scope identifiers; ungranted refs are rejected. Optional public manifest metadata remains unchanged until its exact installed Codex schema is verified.

The 4.0.x package carries the breaking start contract introduced in 4.0.0:
`start_orchestration.task.user_request` is required and preserves the exact
user-authored text. Deprecated `task.objective` is only an exact compatibility
mirror. The runtime also requires final report `questions: []` and routes
material ambiguity through durable `worker_question`; deterministic preflight
holds short underspecified product-surface creation requests until the user
answers a blocking question.

The marketplace validator also enforces the machine-readable shared worker
contract: one strict eight-field report, worker `worker_question` and `record_report`, the three
coordinator lifecycle operations, and scoped predecessor `read_worker_report`,
compact native acknowledgement, exact-root Codebase
Memory resolution, the four profiles allowed one index refresh, and bounded
non-looping fallback. The compact worker schema accepts only validated
project-relative `context_files`, automatically injects available repository
knowledge indexes, and requires `Knowledge reviewed:` evidence. Repository
invariants separately require the bundled
knowledge-harvest census reference and its exhaustive inventory, coverage
matrix, page-depth, structural feature-index, and failure contracts to remain synchronized with the
orchestrator and knowledge-harvest skills.

## Verification

Use the marketplace validator, plugin probe, and installer check listed in [verification.md](../../project/verification.md). Current 6.1.3 evidence is the 309-test suite, marketplace validation, Python compilation, shell syntax, cold boot, an isolated fresh-plugin probe, installed-content verification at `6.1.3+codex.<build>`, and a completed installed-plugin live harvest with eight reports, bounded documentation rework, repeat review, and close verification. A fresh final-build worker-identity dispatch also passed; the installer preserves the user MCP approval override. Run tracked-release verification against the committed candidate before push.
<!-- GENERATED:END -->
