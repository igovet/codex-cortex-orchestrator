---
name: adaptive-pipeline
description: Internal Cortex replanning overlay. Load only for an explicitly activated Cortex task when new evidence requires a future-pipeline decision; never select for ordinary work.
---

# Adaptive Pipeline

Adapt deliberately; do not automate an opaque state machine. The model/effort
policy is a dispatch-time routing decision; adaptive replanning is a separate,
evidence-backed change to future semantic waves.

1. Identify the new evidence and the assumption it invalidates.
2. Decide whether to add a specialist, re-sequence work, widen validation, reduce scope, or, only when the user task itself is ambiguous, stop for a user decision. Internal Cortex policy, gate, planner, retry, routing, ledger, worker, and recovery conditions are coordinator advice and must be delegated or repaired without asking the user.
3. Record the revised plan and why it changed in the parent thread. The
   coordinator must explicitly classify the replacement as material or as a
   no-op/transport-only change; Cortex must not infer materiality from result
   prose.
4. Preserve completed evidence; do not repeat a completed check without a reason.

Typical adaptations:

- New schema or data migration: add `database_architect` before a writer.
- Discovered UI surface: add `ux_designer` or `accessibility_engineer` before review.
- High-risk auth, payments, or sensitive-data change: add `security_auditor` and require explicit verification.
- QA proves the plan wrong: preserve the existing plan in history and let the
  orchestrator choose whether to re-plan, add a specialist, or correct the
  current owner. A stale plan revision, predecessor digest, or semantic
  pipeline digest is advisory evidence for the next corrective dispatch; it
  never forces a Planner recovery or a user reapproval pause.

When approval is explicitly requested by the user, bind it to the plan
revision, plan result ref, verified predecessor evidence digest, and semantic
future-pipeline digest. A replacement may use a Planner wave or another
coordinator-chosen owner after the available evidence; no-op and transport-only
replacements retain the existing approval.

Pipeline rework is unbounded while acceptance criteria, required verification,
or canonical findings remain unresolved. Failure counts are durable audit and
routing evidence, not a retry budget. Cortex raises reasoning effort after
each unresolved cycle (`high`, then `xhigh`, then `max`) and routes eligible
ordinary work to Terra after two failures unless the user explicitly selected
a model. Use a materially different `next_strategy` or replan when evidence
supports it, but never require either merely to authorize another recovery.

Question Firewall: a user question may cover only task requirements, scope,
acceptance/product behavior, or explicit external/destructive authorization.
The worker sends the complete material context as one arbitrary-length text;
it does not classify, enumerate, or structure user choices. Never turn a
policy or lifecycle recommendation into a user question; preserve it as
internal evidence and let the orchestrator choose the next corrective wave.
