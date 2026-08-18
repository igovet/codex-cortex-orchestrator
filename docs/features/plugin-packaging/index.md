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
- [cortex-host-preflight.py](../../../scripts/cortex-host-preflight.py) performs a read-only readiness diagnostic for the Codex CLI, selected Python runtime, source plugin contract, same-user cache, exact `cortex@cortex` registration, MCP approval configuration, and lifecycle-hook trust, plus an explicit MCP readiness summary.
- [probe-fresh-cortex-plugin.py](../../../scripts/probe-fresh-cortex-plugin.py) tests fresh isolated CLI registration when the Codex CLI is available.
- [profiles.json](../../../plugins/cortex/profiles.json) is the canonical profile, adaptive model/effort routing, implementation routing, and generated-catalog contract validated against the bundled TOML files and orchestration skill.
- [feature-census.md](../../../plugins/cortex/skills/knowledge-harvest/references/feature-census.md) defines the bundled exhaustive inventory, coverage-matrix, feature-page, and harvest failure contract.

## Behavior and status

The current source manifest and server are intended for plugin version `9.0.2`. This
repository documentation does not assert that any user's local registration is
installed, updated, or content-verified. The launcher validates `CORTEX_PYTHON` (or `python3`
from `PATH` when unset), requires Python 3.11+ with `tomllib`, and executes the
configured bundled entrypoint. The MCP manifest and all five lifecycle hooks
use this launcher, so they share the selected interpreter.

For host diagnosis, `cortex-host-preflight.py` independently checks that the
same Codex user can resolve a `codex` executable, select a compatible Python,
load a valid source plugin contract, and see matching Cortex metadata in
`CODEX_HOME`. A cached version directory without readable `plugin.json`,
`.mcp.json`, a regular `scripts/cortex.py` entrypoint, or an executable
launcher is reported as a failure rather than treated as proof of installation.
`mcp.status=READY` additionally requires exactly one enabled same-version
`cortex@cortex` registration from `codex plugin list --json`, a regular
`config.toml` with `default_tools_approval_mode = "approve"` and, for a
granular approval policy, `mcp_elicitations = true`, plus all five enabled,
trusted, cache-backed lifecycle hooks with matching persisted hashes.
The JSON expands these requirements into the seven check names documented in
the [SSH host troubleshooting runbook](../../project/ssh-hetzner-troubleshooting.md).
The [SSH host troubleshooting runbook](../../project/ssh-hetzner-troubleshooting.md)
records the failure signatures and same-user recovery sequence; it does not
install software or mutate Codex configuration.

The validator requires one root marketplace plugin entry, a canonical regular plugin directory, a version-aligned manifest/server, an executable launcher, 21 registered profile files with structured playbooks, scope/plan-aware validated gate briefings, and the ten expected skills. For every profile it validates exact TOML identity, description, sandbox parity, and the machine-validated execution contract. The tracked-release archive validator is blocking in CI and rejects runtime state and unsafe archive entries. It validates only committed `HEAD`; the package contract requires the exact eight-tool v5 public surface—the three coordinator lifecycle operations plus worker `worker_question`, `get_report_template`, `record_report`, identity/digest-scoped `read_dispatch_briefing`, and scoped predecessor `read_worker_report`—and aligned plugin/server versions. Briefings and reports are returned only in scoped, server-bounded cursor pages; oversized `max_bytes` requests are clamped to 32768. Worker caller/schema validation errors are structured same-attempt corrections and do not consume recovery attempts; only explicit non-retryable integrity/storage blockers terminate the worker. Optional public manifest metadata remains unchanged until its exact installed Codex schema is verified.

The current start contract requires `start_orchestration.task.user_request` and
preserves the exact user-authored text. If `task.objective` is supplied, request
normalization accepts it only as an exact mirror of `user_request`; it can never
change task identity or expand the worker contract. The runtime also requires
final report `questions: []` and routes material ambiguity through durable
`worker_question`; deterministic preflight holds short underspecified
product-surface creation requests until the user answers a blocking question.

Every gate report includes canonical top-level `gate_result`; the
older `closure` sibling remains a review/close compatibility alias. The report
Markdown renderer escapes HTML and only list-item markers, preserving ordinary
Markdown punctuation rather than introducing backslashes before dots,
parentheses, or hyphens.

The installer enforces Cortex MCP `default_tools_approval_mode = "approve"`
on clean and existing configurations. It preserves the captured value during
remove/add, then writes and verifies `approve`; `--check` fails when the
effective setting is missing or weaker.

The marketplace validator also enforces the machine-readable shared worker
contract: one strict seven-field report, worker `worker_question`,
`get_report_template`, `record_report`, the three
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

Use the host preflight, marketplace validator, plugin probe, and installer check listed in [verification.md](../../project/verification.md) according to the question being diagnosed: host preflight is read-only and can run without installation, while the plugin probe and `sync-cortex.sh --check` are installation/package checks. `sync-cortex.sh --dry-run` reports that it changed no plugin or configuration. The source-mode live commands in that document run against this checkout without installation, reinstallation, or plugin update; their current 9.0.2 results are recorded there, and remaining gates are not implied by this page. The 9.0.2 source candidate requires resolver, launcher, marketplace, shell, cold-boot, deterministic fixtures, benchmark, fresh-plugin, full lifecycle live, and tracked-release checks before release. Its first MCP access applies checksummed project-local SQLite migrations automatically, including the v8 revision-aware orchestration catalog. Pre-SQLite task Markdown/JSON is ignored by the active ledger. Run tracked-release verification against the committed candidate before push.
<!-- GENERATED:END -->
