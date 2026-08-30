# Run2v planner consumption and publication forensic matrix

Sanitized read-only evidence from the isolated diagnostic stream. No handles,
tokens, prompts, report prose, or opaque identifiers are recorded.

| Boundary | Observed | Assessment |
|---|---|---|
| Assignment delivery | `dispatch_brief` present with rendered message, effective contract, planner scope, and bootstrap capability | Authoritative delivery was present |
| Planner scope | Full planner `planning_items` catalogue present (30 items in the run2v assignment) | Scope was not omitted by the server |
| Bootstrap consumption | `consume_assignment_evidence` request used the advertised assignment locator and bootstrap capability fields; server result was successful | Worker bootstrap completed |
| Advertised `publish_plan` contract | Evidence requires schema, summary, verification, risks, deviations, unresolved, nonempty `contract_coverage`, scope, stages, evidence, and documentation impact | Coverage is mandatory for a planner |
| First planner publication | Evidence contained the other plan members but omitted `contract_coverage` entirely | Incomplete evidence, not malformed nesting |
| Server disposition | Publication was rejected before finalization | Correct fail-closed admission |

## Root cause

Run2v successfully consumed its server-owned assignment evidence, but the first
planner publication did not carry the required contract-coverage member. The
failure was therefore after bootstrap consumption, during model-authored
publication assembly. It was not caused by missing authoritative delivery,
invalid bootstrap consumption, or a contradictory tool schema.

## Relation to earlier runs

The earlier run with guessed references supplied a coverage array containing
non-authoritative references. The subsequent run omitted coverage after the
trusted-scope correction. Run2v confirms that successful consumption alone does
not guarantee that the typed scope is preserved into publication evidence.

## Production implication

Keep the server’s complete publication schema and exact-reference admission
strict. The missing architectural guarantee is a durable mapping from the
consumed server scope to the planner’s evidence-construction context; the host
handoff should preserve that scope correlation through the child turn.
