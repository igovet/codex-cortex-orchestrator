# Project overview

Cortex stores tasks, advisory governance and Markdown documents. The model owns
execution and semantic judgment. The complete runtime is packaged below
`plugins/cortex`; repository tooling is development support only.

- [Report storage and reliability](storage.md)
- [Development conventions](conventions.md)
- [Verification](verification.md)
- [MCP contract review](mcp-contract-review.md)
- [Markdown coordination feature](../features/markdown-reports/index.md)
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
routing, preservation of manual docs and source-based completeness checks.
See [knowledge routing](../features/knowledge-routing/index.md),
[decisions](decisions.md) and [gotchas](gotchas.md).
