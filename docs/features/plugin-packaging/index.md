# Plugin packaging and marketplace validation

<!-- GENERATED:START -->
## Purpose

This feature packages Cortex as a repository-local Codex plugin and validates that marketplace metadata, plugin companions, profiles, and skills agree.

## Key files and dependencies

- [plugin.json](../../../plugins/cortex/.codex-plugin/plugin.json) declares the `cortex` plugin, version, skills directory, and MCP companion.
- [.mcp.json](../../../plugins/cortex/.mcp.json) exposes the Python MCP server through the bundled [cortex-launcher](../../../plugins/cortex/scripts/cortex-launcher).
- [marketplace.json](../../../.agents/plugins/marketplace.json) is the sole repository-managed root marketplace entry; it points only to `./plugins/cortex` and is not a personal marketplace.
- [validate-cortex-marketplace.py](../../../scripts/validate-cortex-marketplace.py) validates the root-only marketplace and plugin contract.
- [sync-cortex.sh](../../../scripts/sync-cortex.sh) validates sources, registers the local marketplace, supports a read-only `--check`, and offers a no-write `--dry-run` preview.
- [probe-fresh-cortex-plugin.py](../../../scripts/probe-fresh-cortex-plugin.py) tests fresh isolated CLI registration when the Codex CLI is available.
- [profiles.json](../../../plugins/cortex/profiles.json) is the canonical profile, adaptive model/effort routing, implementation routing, and generated-catalog contract validated against the bundled TOML files and orchestration skill.
- [feature-census.md](../../../plugins/cortex/skills/knowledge-harvest/references/feature-census.md) defines the bundled exhaustive inventory, coverage-matrix, feature-page, and harvest failure contract.

## Behavior and status

The current source manifest and server declare plugin version `6.5.0`. This
repository documentation does not assert that any user's local registration is
installed, updated, or content-verified. The launcher validates `CORTEX_PYTHON` (or `python3`
from `PATH` when unset), requires Python 3.11+ with `tomllib`, and executes the
configured bundled entrypoint. The MCP manifest and all five lifecycle hooks
use this launcher, so they share the selected interpreter.

The validator requires one root marketplace plugin entry, a canonical regular plugin directory, a version-aligned manifest/server, an executable launcher, 21 registered profile files with structured playbooks, 13 validated gate briefings, and the ten expected skills. For every profile it validates exact TOML identity, description, sandbox parity, and the machine-validated execution contract. The tracked-release archive validator is blocking in CI and rejects runtime state and unsafe archive entries. It validates only committed `HEAD`; the package contract requires the exact seven-tool v4 public surface—the three coordinator lifecycle operations plus worker `worker_question`/`record_report`, identity/digest-scoped `read_dispatch_briefing`, and scoped predecessor `read_worker_report`—and aligned plugin/server versions. Briefings and reports are returned only in scoped, server-bounded cursor pages; an incomplete response may continue only with its signed cursor. Optional public manifest metadata remains unchanged until its exact installed Codex schema is verified.

The current start contract requires `start_orchestration.task.user_request` and
preserves the exact user-authored text. If `task.objective` is supplied, request
normalization accepts it only as an exact mirror of `user_request`; it can never
change task identity or expand the worker contract. The runtime also requires
final report `questions: []` and routes material ambiguity through durable
`worker_question`; deterministic preflight holds short underspecified
product-surface creation requests until the user answers a blocking question.

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

Use the marketplace validator, plugin probe, and installer check listed in [verification.md](../../project/verification.md) only when installation/package validation is in scope. `sync-cortex.sh --dry-run` reports that it changed no plugin or configuration. The source-mode live command in that document runs against this checkout without installation, reinstallation, or plugin update. The 6.5.0 source candidate requires resolver, launcher, marketplace, shell, cold-boot, fresh-plugin, and tracked-release checks before release. Its first MCP access applies checksummed project-local SQLite migrations automatically, including the v7 blob/logical-artifact/export catalog. Pre-SQLite task Markdown/JSON is ignored by the active ledger. Run tracked-release verification against the committed candidate before push.
<!-- GENERATED:END -->
