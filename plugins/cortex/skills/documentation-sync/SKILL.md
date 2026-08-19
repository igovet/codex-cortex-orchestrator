---
name: documentation-sync
description: Internal Cortex documentation overlay. Load only after verified changes in an explicitly activated Cortex task require durable project or feature documentation updates.
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
