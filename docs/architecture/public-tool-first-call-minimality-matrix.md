# Public tool first-call minimality matrix

Development-only review record for the 15 semantic Cortex tools. Public
schemas remain the authority; this file is not an installed contract.

| Tool | Questionable field | Server derivation/default | Decision and first-call risk |
|---|---|---|---|
| `open_task` | removed `objective` | Exact `request_original` is persisted as the immutable task objective | Removed: a second model-authored summary duplicated the request, could drift, and caused a real first-call validation failure. |
| All task-scoped tools | `task_ref` | The first exact selector or successful task opening binds connection-local identity; later calls on that connection reuse it | Optional after binding: prevents repeated opaque-locator transcription while a fresh process fails closed and never infers task recency. |
| `read_task` | `after_sequence` | Defaults to the beginning of the bounded timeline | Preserve: optional pagination is a real query choice; omission is safe. |
| `open_clarification` | `assignment_ref` | Omitted value means coordinator/task-origin clarification; the subject is always the anchored task | Preserve: assignment-origin delivery is a genuine causal relation, not derivable from prose. Generic clarification has no public subject selector. |
| `record_clarification` | none | Binding supplies subject and decision family | Preserve strictness: response is the only caller-owned decision data. |
| `open_plan_review` | none | Plan subject is selected from the required typed `plan_ref` | Preserve strictness. |
| `record_plan_review` | none | Binding supplies immutable plan relation | Preserve strictness. |
| `open_steering` | `assignment_ref` | Omitted value means task-level steering | Preserve: assignment-targeted steering is a genuine semantic choice. |
| `record_steering` | `supersedes_decision_ref` | Omitted value means no supersession | Preserve: supersession is explicit durable intent. |
| `open_assignment` | `input_report_refs`, `input_decision_refs` | Omitted means no predecessor evidence; server binds supplied typed predecessors and derives the unique current-owner lineage | Preserve: typed evidence dependency is a real DAG choice. No outcome or predecessor identity routing is public. |
| `open_assignment` | removed `parent_assignment_ref` | Immutable report authorship plus current ownership derives rework/transfer lineage | Removed: caller restatement became stale after ownership transfer and caused a real first-call conflict. The relation remains output evidence. |
| `consume_assignment_evidence` | `cursor` | Omitted means first bounded evidence page | Preserve: pagination is a real query choice. |
| `publish_plan`, `publish_result`, `publish_documentation` | `status` | Defaults to `completed` | Preserve: `partial`/`blocked` are genuine terminal semantic outcomes. Continuation and assignment are mandatory server-issued authority. |
| `assess_governance` | `rationale`, `risk_factors` | Empty rationale/list defaults | Preserve: optional advisory evidence; no identity or routing authority. |
| `close_task` | `completion_notes`, `follow_ups`, `unresolved_risks` | Empty/omitted optional closure detail | Preserve: these are genuine closure evidence, not output shaping. |

No public field except the explicitly connection-bound task selector is an
optional server-derivable opaque identity. No model-routing selection,
outcome-routing map, duplicate objective/language field, budget, or output
format selector remains. Generic clarification is task-scoped and intentionally
has no public `subject_ref`; only the optional assignment relation on a
clarification/steering hold remains because it changes causal delivery
semantics.
