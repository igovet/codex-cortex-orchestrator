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

The current source manifest and server declare plugin version `4.4.0`; the
recorded installed-runtime evidence below does not attest this current
source candidate.

The validator requires one root marketplace plugin entry, a canonical regular plugin directory, a version-aligned manifest/server, 21 registered profile files with structured playbooks, 13 validated gate briefings, and the ten expected skills. For every profile it validates exact TOML identity, description, and sandbox parity plus complete automatic/manual route metadata, gates, and selection/avoidance guidance. The ordered implementation contract must route each of the eight specialist writers exactly once before the `general` fallback, and the generated root-skill catalog must exactly match `profiles.json`. It rejects nested marketplace artifacts, symlinks, and plugin-local `.codex/` runtime state. Before requiring the Codex CLI, `sync-cortex.sh` validates `~/.codex/config.toml` (or the configured `CODEX_HOME` equivalent) as an existing regular, non-symlink path; an unsafe config path fails closed even when the CLI is unavailable. The installer re-registers the local plugin with a remove/add cycle; when present, it captures and restores the user's Cortex MCP `default_tools_approval_mode` override across that cycle and atomically enforces `[agents] default_subagent_model = "gpt-5.6-luna"`. A different default is privately backed up before replacement; comments, unrelated keys, and mode are preserved. Its `--check` mode is read-only and detects installed-content drift, missing or non-Luna global routing configuration, or managed legacy artifacts, while `--dry-run` reports the planned changes without writing. The fresh probe uses a copy of the root checkout with temporary `HOME` and `CODEX_HOME`; it prints `SKIP` if the CLI is absent. The tracked-release archive validator is blocking in CI and rejects runtime state, secret-prone paths, missing release policies, private local home paths in public documents, and unsafe archive entries. It validates only committed `HEAD`; the package contract requires the exact six-tool v3 public surface—the three coordinator lifecycle operations plus worker `worker_question`/`record_report` and coordinator `read_worker_report`—and aligned plugin/server versions. Optional public manifest metadata remains unchanged until its exact installed Codex schema is verified.

The 4.0.x package carries the breaking start contract introduced in 4.0.0:
`start_orchestration.task.user_request` is required and preserves the exact
user-authored text. Deprecated `task.objective` is only an exact compatibility
mirror. The runtime also requires final report `questions: []` and routes
material ambiguity through durable `worker_question`; deterministic preflight
holds short underspecified product-surface creation requests until the user
answers a blocking question.

The marketplace validator also enforces the machine-readable shared worker
contract: one strict eight-field report, worker-only `worker_question` and `record_report`, the three
coordinator lifecycle operations, coordinator-only `read_worker_report`,
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

Use the marketplace validator, plugin probe, and installer check listed in [verification.md](../../project/verification.md). The current source and local plugin registration report `4.4.0+codex.20260815215311`; after the last source edit, `./scripts/sync-cortex.sh --check` correctly reports same-version content drift, and no plugin reinstall command was run in this turn. The current source evidence includes 270 passing tests plus manifest-policy regressions; live-model, tracked-release, and publication evidence remains unverified. Historical 4.0.0 evidence includes 241 passing tests in 15.770 seconds and installed/content-verified cachebuster `4.0.0+codex.20260814231427`; it does not attest the current source candidate.
<!-- GENERATED:END -->
