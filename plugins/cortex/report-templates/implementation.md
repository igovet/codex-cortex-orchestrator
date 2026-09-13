# Implementation Report

<!-- Opening decision brief: conclusion and decisive observations; checked and open requirements; contradictions and limits; what could disprove the conclusion; next action. Keep the opening, including title and marker, within the first bounded report page. Put detailed evidence below. -->

## Inputs consumed

<!-- Name relevant predecessor reports, project evidence, requirements, and constraints. -->

## Changes

<!-- List exact paths and explain behavior, data, interface, and compatibility effects. -->

## Verification

<!-- Record exact commands with cwd and exit codes plus decisive manual or rendered evidence. -->

## State, rollout, and evidence reuse

<!-- Separate implementation evidence, observed delivery receipts, and coordinator acceptance. Optionally record the structured model-authored fields delivery_state, acceptance_boundary, causal_model_delta, predecessor_rollout, retry_discriminator, and receipt_references. Bind Git/CI/deploy/production claims to exact artifact revisions and observed receipts. When relevant, include failed-canary evidence, hypothesis basis/uncertainty/disconfirmation, review/verification reuse identity, and a new-evidence or rerun reason. Missing fields are advisory diagnostics only. -->

## Risks and remaining work

<!-- Record unrun checks, environmental gaps, uncertainty, residual risk, and follow-up work. -->

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
