---
name: adaptive-pipeline
description: Reassess an active coding plan when new repository evidence changes complexity, scope, dependencies, security risk, or validation needs. Use after material discovery, failed verification, or a changed implementation plan.
---

# Adaptive Pipeline

Adapt deliberately; do not automate an opaque state machine.

1. Identify the new evidence and the assumption it invalidates.
2. Decide whether to add a specialist, re-sequence work, widen validation, reduce scope, or stop for a user decision.
3. Record the revised plan and why it changed in the parent thread.
4. Preserve completed evidence; do not repeat a completed check without a reason.

Typical adaptations:

- New schema or data migration: add `database_architect` before a writer.
- Discovered UI surface: add `ux_designer` or `accessibility_engineer` before review.
- High-risk auth, payments, or sensitive-data change: add `security_auditor` and require explicit verification.
- QA proves the plan wrong: use `planner` to re-plan, then retry with the new evidence.

After two failed attempts at the same approach, pause and ask for direction instead of endlessly escalating agents or effort.
