# Knowledge-route contract

<!-- GENERATED:START -->
## Purpose

The Cortex skill defines deterministic `harvest` and `harvest-refresh` routes for source-backed repository knowledge maintenance.

## Key files and dependencies

- [Cortex Orchestrator skill](../../../plugins/cortex/skills/orchestrator/SKILL.md) defines native invocation, activation, route selection, and knowledge-route workflow.
- [Knowledge Harvest skill](../../../plugins/cortex/skills/knowledge-harvest/SKILL.md) defines generated-block preservation and required project/feature documentation.
- [Documentation Sync skill](../../../plugins/cortex/skills/documentation-sync/SKILL.md) limits updates to knowledge affected by completed, verified work.
- [test_marketplace_release_gate.py](../../../tests/test_marketplace_release_gate.py) is the sole release gate for the current package contract.

## Behavior and status

`harvest` updates only missing or stale evidence-backed facts. `harvest-refresh` re-audits allowed documentation, preserves text outside generated markers, validates paths and applicable commands, and requires a second planning pass with no proposed changes. Both routes use Scope before domain discovery; every discovery worker depends on `scope`, architecture consumes `scope` plus `discover`, and the final Plan follows architecture. Harvest-specific role guidance is injected from the conditional `profiles.json` mode overlay and is absent from ordinary worker prompts. The `help` route is read-only; `harvest` and `harvest-refresh` require explicit Cortex activation. Before scanning or dispatching, knowledge routes must bind the exact absolute `project_root` during activation and confirm the host-private Cortex state root for that project; no `CORTEX_ROOT`, `/tmp`, root switch, or unledgered fallback is valid. Knowledge routes start from the schema v19 ledger and do not import task state from unrelated filesystem layouts.

## Verification

Run the marketplace release gate and current prompt evaluators listed in [verification.md](../../project/verification.md); the route contract is also checked by [validate-cortex-marketplace.py](../../../scripts/validate-cortex-marketplace.py).
<!-- GENERATED:END -->
