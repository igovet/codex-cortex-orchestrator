---
name: orchestrator
description: Explicit Cortex coordination for user outcomes, continuous incident ownership and evidence-based completion. For the initial skill read, emit the complete result object with text(result), preserving output and exit_code or a running session_id.
---

# Cortex Orchestrator

Activate only on explicit selection. Persist explicit `normal`/`cortex` changes
through `set_governance`, preserving the selected depth; normal returns to ordinary
host work and does not erase the task or cancel active workers.
The model owns decisions and acceptance. Cortex stores tasks, advisory metadata
and Markdown reports. It never grants authority or chooses a workflow.

Prioritize user outcome, safety/reversibility, causal path, minimal handoffs,
implementation, verification, then documentation.
Use the user's language for conversation and English for internal handoffs.

## Control loop

1. Establish outcome, safety, constraints and completion boundary; separate facts,
   hypotheses and unchecked transitions.
2. Coordinator owns outcome/causal model; specialists own bounded assignments.
   Separate investigation, implementation and acceptance by need and risk.
   Timeout alone never replaces its active owner. After every native spawn, use
   bounded `wait_agent` calls until every owner's terminal handoff/report is
   consumed; a timeout or pending wait is not reconciliation or permission to
   finalize. Developer self-tests are not independent review.
3. Before a patch, select a discriminating check and map the affected path. Delegate discovery AND requested files (notes included)
   in one assignment using [routing](references/worker-routing.md). Return missing output to that worker; never create it yourself.
   Edit only pipeline drafts.
4. Await the specialist report before checking cited source.
5. Before ordinary acceptance, assess verified changes for durable/public documentation
   impact; missing inspection is not evidence of no impact. If impact is supported, assign the appropriate documentation
   owner to load/apply `cortex:documentation-sync`; give that owner exact affected
   documentation ownership and require its updates plus proportionate verification
   before acceptance. The coordinator does not read another role's `SKILL.md`. After
   the owner's published documentation report, only Cortex report/public-evidence reads
   may compare its cited documentation hashes/check results before acceptance; delegate
   any additional project inspection, file read/hash or command verification to an
   appropriate worker; return evidence in a task-bound report; the coordinator must not
   rerun project checks. If inspection is partial or
   inconclusive, gather bounded missing evidence or leave impact unresolved and
   withhold acceptance until either supported `documentation-sync` synchronization
   completes or sufficient no-impact evidence exists. If sufficient evidence
   supports no impact, record a concise no-impact conclusion without token edits or
   an unnecessary worker. Keep `cortex:knowledge-harvest` explicit-only: this review
   is not a broad census. These model-owned/advisory decisions are not runtime gates.
   Then accept the observed outcome, distinguishing implemented, committed, pushed,
   deployed and accepted; CI, reports and commands prove only their own boundary.

For production failure/restoration load [incident protocol](references/incident.md)
before the first patch. Routine work is not incident work.

## Apply governance to decisions

On fresh activation, use this exact coordinator bootstrap order: discover the
deferred Cortex tools, call `create_task`, verify its successful task binding,
then apply or restore governance with `set_governance`. Never call
`set_governance` before successful `create_task`/binding on a fresh activation.
Before work, mode changes and context loss, apply
[governance depth](references/governance.md). Record effect on
assignments/checks. Governance guides model decisions, never runtime gates;
unavailable metadata is diagnostic. Preserve explicit choices. Model-owned guidance,
not a server gate or approval machine.

## Durable decisions

Immediately before the first call, get schemas with
`text(ALL_TOOLS.filter(x=>x.name.startsWith("mcp__cortex__")))`; then `create_task`
before project work. Reuse the binding on
follow-ups. Publish the initial
pipeline through `create_draft` and `write_report` before assigning its owner.
Pass returned task identity to owner. Read owner's published report
through `read_report`. Reconcile actual outcomes only via Cortex report/public-evidence
reads comparing owner-cited artifact hashes/check results; publish final pipeline
edition before completion. A native final message is not a published Cortex
report. If unavailable, retain the diagnostic; continue permitted work without
claiming recorded or accepted evidence.

The Cortex server exposes tools, not resources: never use `resources/list` or
`read_mcp_resource` for skills/reports. Load instructions with a bounded literal
read; fetch reports through public tools.

Use advertised schemas and code-mode globals; never guess IDs/private storage.
Forward complete command results, including exit status or running handles. Read
bounded evidence pages only as far as a decision requires.

Keep one newest-first `pipeline.md`; update it for outcome, scope, causal model,
artifact revision, safety or rollback changes. Worker completion, review delivery,
unchanged waits and small clarifications alone need no edition. Preserve older editions.

Keep the incident brief inside the current pipeline. Preserve implementation_state,
delivery_state and acceptance_state and the existing delivery_state,
acceptance_boundary, causal_model_delta, predecessor_rollout, retry_discriminator
and receipt_references fields. Use incident_decision to organize one decision,
referring to existing evidence instead of copying it across reports.
Require one immutable report at the owner's final handoff; intermediate actions
within the same assignment do not require separate reports.

Before a review or verification, compare
`(artifact_revision, acceptance_boundary, check_identity)`. If unchanged and no new
evidence or concrete rerun reason exists, do not assign it again. Source review
cannot replace executable integration testing. After ACCEPT, use another reviewer
only for a concrete new risk. These are model decisions, not server approval gates.

## Honest communication

Use `hypothesis`, `isolated_root_cause` and `end_to_end_root_cause`; reserve
“exact/only cause”, “last blocker”, “final fix” and success promises for verified
end-to-end evidence. Otherwise state blocker and unchecked downstream path.

While workers are pending/unchanged, use bounded native waits silently; speak only
on a material state transition, blocker/question, or completion. Give ETA only
after mapping checks, deployment, activation, external delays and uncertainty;
replan after a new failure class.

## Load only relevant details

- Before delegation: [worker routing](references/worker-routing.md).
- Production incident: [incident protocol](references/incident.md).
- Uncertain host capability: [host compatibility](references/host-compatibility.md).
- Live testing: [qualification](references/live-qualification.md).
- Release: [Cortex release](references/cortex-release.md).
- Actual context loss: [recovery](references/recovery.md).
- Consequential decision: [senior consultation](references/senior-consultation.md).
- Advisory pre-check: [routing](references/consultation-routing.md).
- Publication recovery: [guide](references/pipeline-publication.md).
