---
name: documentation-sync
description: Synchronize durable project, feature, verification, gotcha, and ADR documentation with completed code changes. Use after C2/C3 work or whenever behavior, interfaces, architecture, project commands, or feature ownership changed.
---

# Documentation Sync

Update only the durable knowledge changed by completed, verified work.

## Workflow

1. Read the diff, acceptance criteria, verification evidence, and affected existing docs.
2. Identify whether the change affects a feature contract, public behavior, project structure, commands, conventions, gotchas, or an architectural decision.
3. Dispatch `technical_writer` with only the relevant paths. It may update `docs/features/<name>/index.md`, `docs/project/verification.md`, `docs/project/gotchas.md`, `docs/project/decisions.md`, and registries as warranted.
4. Preserve manual text and update only generated blocks unless the task explicitly authorizes a manual-doc change.
5. Re-read the edited docs and ensure file references, commands, and links match the diff.

Skip this workflow for a local C1 fix that changes none of the above. Do not create documentation merely to record that a task occurred.
