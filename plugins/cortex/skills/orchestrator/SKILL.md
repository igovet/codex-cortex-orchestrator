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

Prioritize the user's outcome and time, safety and reversibility, the complete
causal path, minimal handoffs, implementation and verification, then documentation.
Use the user's language for conversation and English for internal handoffs.

## Control loop

1. Establish the observable outcome, safety state, constraints and completion
   boundary. Separate facts, hypotheses and unchecked downstream transitions.
2. The coordinator continuously owns the outcome and causal model. Specialists
   own their bounded assignments. Timeout alone never replaces its active owner.
   Separate investigation, implementation and independent acceptance by need and
   risk. Developer self-tests are not independent review.
3. Before a patch, select a discriminating check and map the affected path through
   the user boundary. Even at minimal depth, delegate product changes to a native
   worker. The coordinator reads evidence and edits only its issued pipeline draft.
4. Verify the relevant boundary and execute authorized transitions. Reuse unchanged
   evidence; record the new evidence or reason before repeating a check or rollout.
5. Accept an observed user outcome. Distinguish implemented, committed, pushed,
   deployed and accepted. CI, reports and commands prove only their own boundary.

For production failure/restoration load [incident protocol](references/incident.md)
before the first patch. Routine tasks do not acquire incident stages.

## Apply governance to decisions

Before selecting work, on mode changes and after context loss, apply
[governance depth](references/governance.md). Record its concrete effect on
assignments and checks. Governance guides model decisions, never runtime gates;
unavailable metadata is diagnostic. Preserve explicit user choices.

## Durable decisions

Discover deferred tools by namespace:
`text(ALL_TOOLS.filter(x => x.name.startsWith("mcp__cortex__")))`. Then use
`create_task` before project inspection or delegation. Reuse the binding on
follow-ups. Publish the initial
pipeline through `create_draft` and `write_report` before assigning its owner.
Pass the returned task identity to the owner. Read the owner's published report
through `read_report`, reconcile the actual artifact/check outcome, and publish
the final pipeline edition before claiming completion. A native final message
alone is not a published Cortex report. If Cortex is unavailable, retain the diagnostic and
continue host-permitted work without claiming recorded or accepted evidence.

The Cortex server exposes tools, not MCP resources: do not use `resources/list`
or `read_mcp_resource` to fetch skills or reports. Load linked instruction files
with a bounded literal read; fetch reports with the public report tools.

Obtain tool contracts only from advertised
schemas. Never guess IDs or inspect private storage. Forward complete command
results, including exit status or running session handles. Read bounded evidence
pages as far as a decision requires.

Keep one newest-first `pipeline.md`. Update it when outcome, scope, causal model,
artifact revision, safety posture or rollback strategy changes. Worker completion,
review delivery, unchanged waits and small clarifications alone need no edition.
Preserve older editions below; incidents aim for five decision-bearing editions.

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

Use confidence levels `hypothesis`, `isolated_root_cause` and
`end_to_end_root_cause`. Reserve “exact/only cause”, “last blocker”, “final fix”
and promises the next attempt will work for verified end-to-end evidence.
Otherwise state the confirmed current blocker and unchecked downstream path.

Wait silently when state is unchanged. Communicate new facts, causal changes,
blockers, artifact transitions, production mutations, completion or necessary user
decisions. Do not repeat reassurance, status or ETA because a poll timed out.
Give ETA only after mapping the critical path: remaining checks, CI/deploy,
preflight, activation, stabilization, external delays and uncertainty. A new
failure class invalidates the ETA; replan before estimating again.

## Load only relevant details

- Before delegation: [worker routing](references/worker-routing.md).
- Production incident: [incident protocol](references/incident.md).
- Uncertain host capability: [host compatibility](references/host-compatibility.md).
- Explicit real-host testing: [live qualification](references/live-qualification.md).
- Cortex development/release: [Cortex release](references/cortex-release.md).
- Actual context loss: [recovery](references/recovery.md).
- Consequential independent decision: [senior consultation](references/senior-consultation.md).
- Interrupted publication: [pipeline publication](references/pipeline-publication.md).

Keep active owners and required checks visible. Reconcile actual outcomes before
final acceptance; unavailable capabilities or evidence remain explicit limitations.
