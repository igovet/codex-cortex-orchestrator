# Verification Report

<!-- Opening decision brief: conclusion and decisive observations; checked and open requirements; contradictions and limits; what could disprove the conclusion; next action. Keep the opening, including title and marker, within the first bounded report page. Put detailed evidence below. -->

## Scope and expected behavior

<!-- Map checks to requirements, changed surfaces, and named environments. -->

## Executed checks

<!-- Record exact commands, cwd, exit codes, decisive output, and manual observations. -->

## Evidence identity and delivery state

<!-- Record the structured model-authored fields delivery_state, acceptance_boundary, causal_model_delta, predecessor_rollout, retry_discriminator, and receipt_references when applicable. Also record (artifact_revision, acceptance_boundary, check_identity), its observed receipt, and any new-evidence/rerun reason. Separate verification/implementation evidence, delivery receipts, and coordinator-owned acceptance; missing fields are advisory diagnostics only. -->

## Failures and gaps

<!-- Preserve nonzero results, unsupported environments, checks not run, and contradictions. -->

## Assessment

<!-- Explain coverage, remaining risk, blockers, and the next required action. -->

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
