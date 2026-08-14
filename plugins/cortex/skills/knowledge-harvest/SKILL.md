---
name: knowledge-harvest
description: Create or refresh repository knowledge documentation from verified code facts. Use when onboarding a repository, after large restructuring, or when `docs/project` and `docs/features` are absent or stale.
---

# Knowledge Harvest

Create durable repository knowledge, not a task journal. Write it under versioned `docs/` so it is usable by people and every coding harness.

## Modes

- **Harvest:** incrementally create or update only missing, stale, or
  contradicted project and feature documentation after a targeted scan.
- **Refresh:** fully re-audit every allowed document from source, then repeat
  the planning pass to prove idempotence while preserving human-authored notes.

## Workflow

1. Dispatch `explorer` in read-only mode to detect the stack, package manager, entry points, source layout, build configuration, tests, deployment files, and feature boundaries.
2. Have `technical_writer` verify the evidence and update only these files as justified:

```text
docs/project/index.md
docs/project/conventions.md
docs/project/verification.md
docs/project/decisions.md
docs/project/gotchas.md
docs/features/index.md
docs/features/<feature>/index.md
```

3. Discover features from real modules, routes, bounded components, services, controllers, or tests. Avoid treating generic utilities, generated files, or every folder as a feature.
4. Link feature docs to key files and dependencies. Mark uncertain relationships as unknown; do not invent ownership or behavior.

## Preservation rules

Use `<!-- GENERATED:START -->` and `<!-- GENERATED:END -->` for facts that can be refreshed. Preserve text outside that block. Never overwrite an ADR, a manually recorded gotcha, or a feature explanation without evidence and explicit scope. Keep code snippets short and avoid secrets.

## Required content

- `index.md`: purpose, stack, directory map, entry points, environment requirements, feature registry.
- `conventions.md`: observed patterns and tooling, not aspirational rules.
- `verification.md`: actual install, build, lint, typecheck, test, and local-run commands, with source locations.
- Feature docs: purpose, key files, dependencies, status, verification, and related decisions.

Return a summary of generated and preserved files, missing evidence, and follow-up scans worth doing.
For a refresh, also report full-scan coverage and prove that a second planning
pass proposes no changes.

When run through Cortex, each worker publishes the exact `cortex/report/v1`
fields through `record_report`. Grant only report bodies the next attempt
needs, tie C2/C3 evidence to its one-use receipt, and reconcile the report bus
and project manifest before close. Reports may cite source paths but must not
contain secrets, personal data, or source dumps.
