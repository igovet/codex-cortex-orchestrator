---
name: documentation-sync
description: Update affected project knowledge after verified changes while preserving manual documentation.
---

# Documentation synchronization

## Responsibility

| Role | Work |
| --- | --- |
| Coordinator | Select scope and specialists from documentation-impact previews and the current pipeline |
| Technical writer | Confirm source-backed facts and update only affected documentation |
| Independent reviewer when warranted | Check material documentation changes against source, commands and links |

The coordinator does not read indexes, files or report bodies, inspect diffs or
edit documents itself. Delegate
missing evidence. A stale or absent index does not automatically require harvest.

## Inputs for the writer

- Verified behavior, architecture, interface, command or ownership changes.
- Exact affected index/page paths, requirements and preservation boundaries.
- Relevant reports, source routes and known uncertainties.

## Work sequence

1. Read applicable indexes and linked pages for the assigned scope.
2. Confirm consequential claims in source, tests or executable configuration.
3. Preserve manual material outside generated blocks unless its change is authorized.
4. Update durable knowledge, public guidance and examples only where affected.
5. Validate changed links, anchors, commands, terminology and stated behavior.
6. Publish an ordinary Markdown report using the
   [documentation example](../cortex-control/references/documentation-example.md).

## Verification and completion

- Scale independent checking to actual impact; no server stage or publication kind exists.
- A supported no-impact conclusion can use existing evidence without a token edit or extra worker.
- Missing inspection is not proof of no impact. State unverified facts and unrun checks.
- Required updates and their verification must finish before task completion.
- Reflect findings and follow-up in the single pipeline and final result.
- Harvest already owns its documentation work; do not duplicate it under another label.
