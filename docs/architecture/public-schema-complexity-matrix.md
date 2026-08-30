# Public Semantic Schema Complexity Matrix

Development-only audit of the 15 advertised Cortex operations. This document
is not a runtime contract and must not be loaded into skills, prompts, or a
shipped package.

## Audit method

The matrix was extracted read-only from `PUBLIC_TOOLS` and its advertised input
schemas/descriptions on 2026-08-29. Required and optional properties below are
observations, not alternate calling instructions. The live advertised schema
remains the only authority for a call.

| Operation | Required properties | Optional properties / branches | Complexity | Finding and safe recommendation |
| --- | --- | --- | --- | --- |
| `open_task` | 7 | `context` | Low | The redundant top-level contract field is already absent. Keep the server-derived verification evidence behavior; do not add a client field merely for symmetry. |
| `read_task` | 1 | `after_sequence` | Low | One anchor plus bounded chronology cursor. Keep; cursor is query pagination, not duplicated identity. |
| `open_clarification` | 3 | `subject_ref`, `assignment_ref` | Medium | Two optional relation anchors represent distinct question ownership cases. Keep, but reject contradictory combinations server-side and document only semantic purpose. |
| `record_clarification` | 4 | none | Low | One hold anchor and one exact observed answer relation. Keep. |
| `open_plan_review` | 4 | none | Low | Plan identity and review binding are distinct server concepts. Keep. |
| `record_plan_review` | 5 | none | Low | Binding, outcome, and observed response are distinct. Keep. |
| `open_steering` | 3 | `assignment_ref` | Medium | Optional assignment ownership is a legitimate semantic distinction. Keep; server must derive the task relation. |
| `record_steering` | 5 | `supersedes_decision_ref`; nested two-branch steering delta | High | The delta is the only nested branch. Preserve atomic supersession, but prefer one closed semantic delta envelope if future evidence shows repeated first-call confusion. Do not add parallel aliases. |
| `open_assignment` | 8 | `input_report_refs`, `input_decision_refs`, `parent_assignment_ref` | Very high | Live #1d shows the model can supply routing/context fields while omitting the required objective. The envelope mixes worker intent (`objective`, `scope`, `instructions`) with coordinator/server routing (`role`, `profile_name`, `model`, `reasoning_effort`). This is a first-call ergonomics defect even without duplicate anchors. See the corrected analysis below. |
| `consume_assignment_evidence` | 1 | `cursor` | Low | One assignment anchor and a bounded continuation cursor. Keep. |
| `publish_plan` | 2 | `status` | Medium | The same publication envelope is shared by the three publication operations. Keep one semantic evidence contract; server owns internal chunking and manifest creation. |
| `publish_result` | 2 | `status` | Medium | Same conclusion as `publish_plan`; no additional result-specific parallel fields are needed. |
| `publish_documentation` | 2 | `status` | Medium | Same conclusion as the other publication operations; documentation impact belongs inside the closed evidence contract. |
| `assess_governance` | 2 | `rationale`, `risk_factors` | Medium | Mode plus optional advisory context is coherent. Keep non-blocking semantics; do not turn advisory fields into lifecycle gates. |
| `close_task` | 3 | `unresolved_risks`, `follow_ups`, `completion_notes` | Medium | Closure evidence and follow-up context are distinct. Keep; server derives readiness from immutable publications and pending decisions. |

## Cross-operation findings

| Concern | Observed state | Recommendation |
| --- | --- | --- |
| Parallel identity fields | Task, assignment, decision, and publication operations use one anchor appropriate to their semantic scope. No operation advertises two interchangeable top-level anchors. | Preserve this rule. Do not add compatibility aliases or reconstructed identifiers. |
| Nested alternatives | Only the steering delta advertises a two-branch nested shape. | Keep the existing closed semantic delta for now; consider flattening only as a separately versioned schema change backed by first-call evidence. |
| Optional ambiguity | Optional fields are relation-specific (`subject_ref`, assignment ownership, predecessor evidence, cursors, advisory context). | Keep optionality where omission has a defined server meaning. Reject conflicting combinations before mutation. |
| Client-supplied server facts | The server already derives verification evidence, internal chunking, manifests, readiness views, and command receipts. | Do not expose new client fields for these facts. |
| Limits and budgets | Query cursors are bounded continuation state. Publication chunking is server-owned. | Keep operational bounds server-side and out of model-facing instructions; do not add caller budget fields. |
| Shared publication surface | Plan, result, and documentation publication share the same semantic evidence admission model. | Keep the shared closed evidence contract and operation-specific purpose; avoid three divergent recipes. |
| First-call ergonomics | `open_task` has one task contract with the redundant top-level field removed. Other low-complexity operations are single-anchor calls. | Prioritize schema descriptions and returned handles over further parameter removal. Any removal must prove that user intent remains lossless. |

## Corrected assignment-envelope analysis after live #1d

The previous audit treated the assignment fields as one intentional explicit
worker contract. That conclusion was incomplete. A model-facing first call
must distinguish three semantic layers:

| Layer | Current fields | Risk | Safe architectural direction |
| --- | --- | --- | --- |
| Worker intent | `objective`, `scope`, `instructions` | Three overlapping free-text descriptions can cause omission or inconsistent duplication. | Define one primary worker objective and make supplemental scope/guidance either derived or clearly subordinate. Preserve all user intent when consolidating. |
| Worker identity/profile | `role`, `profile_name` | A free-form role and selected profile can describe the same worker identity while disagreeing. | Let the server validate the profile and derive the role label where possible; do not make the model repeat equivalent identity concepts. |
| Dispatch controls | `model`, `reasoning_effort` | These are host/model-routing controls rather than project intent. Requiring both increases first-call failure and encourages prompt/schema leakage. | Keep coordinator-owned routing in the server-issued dispatch brief or a single routing decision, with host schema authority. Remove caller requirements only after a versioned black-box proof that routing remains deterministic. |
| Evidence lineage | predecessor report/decision references and parent assignment | These are distinct causal inputs, but long optional lists add cognitive load. | Keep typed server-issued lineage; derive parent linkage and predecessor evidence from the durable assignment context where the server already owns it. |

This is a recommendation, not a production change. The safest first step is a
server-owned assignment-intent receipt that returns the complete normalized
dispatch brief, followed by a separately verified routing decision. A later
schema revision may collapse redundant free-text fields, but must preserve
worker ownership, DAG ordering, evidence handoff, profile specialization,
model routing, recovery/rework, and publication behavior.

## Next live first-call risks

1. `open_assignment` is the highest-risk first-call surface: a model may omit
   the required objective while attempting to fill routing and worker-context
   fields. The live verifier must treat that as a schema ergonomics failure,
   not as permission to reconstruct the missing objective.
2. `record_steering` remains the next most complex call because its nested delta
   branch and optional supersession relation combine response recording with
   contract revision.
3. Publication calls share one closed evidence envelope; their first-call risk
   is completeness of evidence, not redundant top-level identity.
4. Decision-recording calls are comparatively low risk once the server-issued
   binding is reused exactly; creating a replacement binding is not recovery.

## Safe change threshold

No production schema change is recommended by this audit. The current largest
ergonomics risks are descriptive salience around relation-specific optional
fields and the nested steering delta, not redundant top-level task creation
data. Any future change should first add a black-box first-call regression,
compare the advertised schema and output evidence, and prove that all
orchestration capabilities remain intact.
