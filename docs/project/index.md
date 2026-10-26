# Project overview

Cortex stores tasks, advisory governance and Markdown documents. The model owns
execution and semantic judgment. The complete runtime is packaged below
`plugins/cortex`; repository tooling is development support only.

- [Report storage and reliability](storage.md)
- [Development conventions](conventions.md)
- [Verification](verification.md)
- [Outcome-driven incident coordination](incident-outcomes.md)
- [Marketplace skills and native agent capacity](host-compatibility.md)
- [Comparative outcome evaluation](quality-evaluation.md)
- [Phase 5 adaptive selection (offline)](phase5-adaptive-selection.md)
- [MCP contract review](mcp-contract-review.md)
- [Markdown coordination feature](../features/markdown-reports/index.md)
- [Optional Advisory Lane](../features/advisory-lane/index.md)
- [Release evidence](../release-readiness.md)

The implementation consists of the advertised [contracts](../../plugins/cortex/scripts/cortex_runtime/contracts.py),
[store](../../plugins/cortex/scripts/cortex_runtime/store.py),
[host user-source reader](../../plugins/cortex/scripts/cortex_runtime/host_source.py),
[MCP server](../../plugins/cortex/scripts/cortex_runtime/server.py),
[coordinator skill](../../plugins/cortex/skills/orchestrator/SKILL.md) and
[shared worker protocol](../../plugins/cortex/agent-sources/worker-protocol.md).
The separate [control skill](../../plugins/cortex/skills/cortex-control/SKILL.md)
is limited to retention maintenance and optional report examples.

Knowledge maintenance retains `harvest` and `harvest-refresh`, index-driven
routing, preservation of manual docs and source-based completeness checks. Ordinary
completion now includes a model-owned review of verified changes for documentation
impact: supported impact assigns the appropriate documentation owner to load/apply
`documentation-sync`, and after that owner's report the coordinator uses only Cortex
report/public-evidence reads to compare owner-cited documentation hashes and check
results before acceptance. Additional project inspection, file read/hash or command
verification is delegated to an appropriate worker and returned in a task-bound report;
the coordinator does not rerun project checks itself. Sufficient no-impact evidence is
recorded without an unnecessary worker. Harvest remains
explicit-only. Partial or inconclusive inspection leaves impact unresolved and
withholds acceptance until bounded evidence, synchronization, or sufficient no-impact
evidence resolves it.
See [knowledge routing](../features/knowledge-routing/index.md),
[decisions](decisions.md) and [gotchas](gotchas.md).

- [Orchestration review](orchestration-review.md): primary guidance, observed faults and bounded corrections.

- [Local lifecycle hooks](../features/lifecycle-hooks/index.md)
