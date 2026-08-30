# Cortex MCP first-call audit (development note)

This matrix is a source-maintenance aid only. The executable catalogue remains
the authority. Every public operation has a closed request object; the model
must form the request from that operation's advertised schema and descriptions.

| Operation | Canonical first request | Server-issued continuation | Generic sibling envelope rejected |
| --- | --- | --- | --- |
| `open_task` | task contract | `handles.task_ref` | yes |
| `read_task` | task locator | none | yes |
| `open_clarification` | task locator + question | binding handle | yes |
| `record_clarification` | task + binding + answer | decision receipt | yes |
| `open_plan_review` | task + plan + review question | binding handle | yes |
| `record_plan_review` | task + binding + outcome | decision receipt | yes |
| `open_steering` | task + steering question | binding handle | yes |
| `record_steering` | task + binding + delta | decision receipt | yes |
| `open_assignment` | task + mission | assignment dispatch | yes |
| `consume_assignment_evidence` | assignment locator | worker continuation | yes |
| `publish_plan` | assignment + continuation + canonical plan evidence | plan publication / approval relation | yes; nested `metadata` regression covered |
| `publish_result` | assignment + continuation + canonical result evidence | result publication | yes |
| `publish_documentation` | assignment + continuation + canonical synthesis evidence | documentation publication | yes |
| `assess_governance` | task + governance mode | assessment receipt | yes |
| `close_task` | task + verdict + closure evidence | closure receipt | yes |

The conformance suite validates one schema-complete request per row and checks
that undeclared generic fields (`evidence`, `metadata`, `idempotency`, `token`,
and `budget`) fail closed where they are not part of that operation. The
publication regression specifically adds `metadata` beside the canonical
`publish_plan.evidence` object and requires rejection before handler logic.

Tool descriptions explicitly state that the request object is canonical and
closed. They do not teach a second envelope or infer fields from another tool.
This keeps argument contracts in the advertised MCP schema/property
descriptions while preserving all semantic orchestration behavior.
