# Feature index

| Feature | Runtime owner | Entry points | Source evidence | Documentation | Verification | Status |
| --- | --- | --- | --- | --- | --- | --- |
| Markdown storage and retention | Local Python runtime | Seven MCP operations; explicit cleanup command | plugins/cortex/scripts/cortex_runtime/ | [Reports](markdown-reports/index.md) | tests/test_markdown_store.py; tests/test_source_reports.py; tests/test_retention.py; tests/test_stdio.py; tests/test_thread_context.py | documented |
| Coordination and project knowledge | Model and bundled skills | Explicit orchestration, help, harvest, refresh, clear, normal | plugins/cortex/skills/; plugins/cortex/agents/ | [Knowledge routing](knowledge-routing/index.md) | Package checks and real-host scenarios | documented |

## Inventory totals

Two feature groups: seven storage operations, one host-side retention command,
22 specialist profiles, and knowledge/routing skills. Two documented groups.

## Unmapped surfaces

No additional installable runtime surface found in the current package manifest.

## Exclusions

Repository packaging, CI and live transport are development support, described
in the project verification guide rather than product features.

## Known unknowns

Real-host qualification is recorded separately in release-readiness.md.

- [Local lifecycle hooks](lifecycle-hooks/index.md)
