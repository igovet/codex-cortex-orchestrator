---
name: adaptive-pipeline
description: Internal Cortex replanning overlay. Load only for an explicitly activated Cortex task when new evidence requires a future-pipeline decision; never select for ordinary work.
---

# Adaptive Pipeline

Adapt deliberately; do not automate an opaque state machine. The model/effort
policy is a dispatch-time routing decision; adaptive replanning is a separate,
evidence-backed change to future semantic waves.

1. Identify the new evidence and the assumption it invalidates.
2. Decide whether to add a specialist, re-sequence work, widen validation, reduce scope, or stop for a user decision.
3. Record the revised plan and why it changed in the parent thread. The
   coordinator must explicitly classify the replacement as material or as a
   no-op/transport-only change; Cortex must not infer materiality from report
   prose.
4. Preserve completed evidence; do not repeat a completed check without a reason.

Typical adaptations:

- New schema or data migration: add `database_architect` before a writer.
- Discovered UI surface: add `ux_designer` or `accessibility_engineer` before review.
- High-risk auth, payments, or sensitive-data change: add `security_auditor` and require explicit verification.
- QA proves the plan wrong: use the final `planner` to re-plan, preserve the
  previous plan/approval in history, reset required approval to `pending_plan`,
  and stop after the replacement Planner until the user approves again. A
  stale plan revision, predecessor digest, or semantic pipeline digest blocks
  post-plan dispatch with recoverable reapproval guidance.

For required approval, approval is bound to the plan revision, plan report ref,
verified predecessor evidence digest, and semantic future-pipeline digest. A
replacement must be a singleton Planner wave after scope, discovery, and all
pre-implementation design gates. No-op and transport-only replacements retain
the existing approval.

Pipeline rework is unbounded while acceptance criteria, required verification,
or canonical findings remain unresolved. Failure counts are durable audit and
routing evidence, not a retry budget. Cortex raises reasoning effort after
each unresolved cycle (`high`, then `xhigh`, then `max`) and routes eligible
ordinary work to Terra after two failures unless the user explicitly selected
a model. Use a materially different `next_strategy` or replan when evidence
supports it, but never require either merely to authorize another correction.
