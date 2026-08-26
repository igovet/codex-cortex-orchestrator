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
5. After reading the completed wave, make one evidence-frontier decision when
   a material change is required. Use `revise_future_pipeline` only for
   unexecuted future waves. If a completed canonical result itself needs
   product correction, use `append_rework_wave` so rework and independent
   verification are appended after the completed history. Exact replay is
   idempotent; a conflicting or stale decision is rejected. Never rewrite an
   executing or completed wave.
6. Dispatch every newly returned worker through native V2 `spawn_agent`. Use
   300-second generic `wait_agent` cycles for ordinary progress; wait output is
   not lifecycle evidence. After the matching terminal `SubagentStop` and
   canonical result are durable, read the canonical wave, then execute required
   governance closure before final handoff.

Typical adaptations:

- New schema or data migration: add `database_architect` before a writer.
- Discovered UI surface: use `ux_designer` for interaction design,
  `accessibility_auditor` for independent inspection or verification, or
  `accessibility_fixer` for accepted remediation.
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

Product rework is distinct from technical recovery. A completed
`needs_rework` result uses `append_rework_wave`; a material change to untouched
future work uses `revise_future_pipeline`. Transport, host-observation, model,
and other technical failures use only the server-owned bounded
Luna-to-Terra-to-Sol replacement ladder for the exact assignment occurrence.
Cortex keeps the selected profile when its capability matches the operation and
otherwise resolves an operation-capable profile from the canonical registry.
The coordinator never turns that technical replacement into product rework or
a future-pipeline revision.

Question Firewall: a user question may cover only task requirements, scope,
acceptance/product behavior, or explicit external/destructive authorization.
The worker sends the complete material context as one arbitrary-length text;
it does not classify, enumerate, or structure user choices. Never turn a
policy or lifecycle recommendation into a user question; preserve it as
internal evidence and let the orchestrator choose the next corrective wave.
