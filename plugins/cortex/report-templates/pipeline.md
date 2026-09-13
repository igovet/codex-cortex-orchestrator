# Current Pipeline

{{CURRENT_OBJECTIVE_AND_STATUS}}

## Requirements and constraints

{{CURRENT_REQUIREMENTS_AND_CONSTRAINTS}}

## Work graph

{{CURRENT_WORK_GRAPH}}

## Assignments and routing

{{CURRENT_ASSIGNMENTS_AND_ROUTING}}

## Evidence and verification

{{CURRENT_EVIDENCE_AND_VERIFICATION}}

## Model-owned state and evidence reuse

<!-- Record implementation_state, delivery_state, and coordinator-owned acceptance_state separately. Record selected governance depth, its rationale report and concrete effects on assignments/checks, distinguishing developer self-tests from independent verification. Optionally structure model-authored evidence as delivery_state, acceptance_boundary, causal_model_delta, predecessor_rollout, retry_discriminator, and receipt_references; map Git/CI/deploy/production claims to observed receipts and exact artifact revisions. Include review/verification reuse identity (artifact_revision, acceptance_boundary, check_identity), plus fresh evidence or a rerun reason when unchanged work repeats. Missing fields and duplicate reuse are advisory diagnostics only. -->

## Decisions, alternatives, discriminating checks, and remaining work

{{CURRENT_DECISIONS_QUESTIONS_AND_REMAINING_WORK}}

## Incident decision (when handling a production incident)

<!-- Keep this compact block in the current pipeline or final owner handoff when applicable. Reference existing delivery_state, acceptance_boundary, causal_model_delta, predecessor_rollout, retry_discriminator and receipt_references instead of duplicating them. Fields guide the model and quality evaluation only; missing fields never create a server gate. -->

```yaml
incident_decision:
  outcome: null
  safety_state: null
  artifact_revision: null
  deployed_revision: null
  runtime_identity: null
  confidence: hypothesis
  causal_model: null
  full_path_covered: false
  unchecked_downstream_predicates: []
  production_snapshot: null
  rehearsal: null
  rollback: null
  predecessor_attempt: null
  new_discriminator: null
  next_action: null
```
