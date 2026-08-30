# Run2r planner publication forensic matrix

Development-only sanitized comparison of run2p and run2r. No identifiers,
handles, prompts, report prose, or raw payload values are retained here.

| Check | Run2p | Run2r | Finding |
|---|---:|---:|---|
| Planner dispatch brief present | yes | yes | Assignment/dispatch succeeded |
| Effective contract revision present | yes | yes | Renderer supplied typed scope |
| Planner `planning_items` catalogue | present (25 items) | present (25 items) | Scope was not omitted or mangled |
| `publish_plan` evidence object | present | present | Top-level evidence serialization succeeded |
| `contract_coverage` member | present (18 entries) | absent | Run2r failed by omission, not nesting or schema coercion |
| Other required v3 evidence members | present | present | Failure isolated to coverage member |
| First request disposition | rejected | rejected | Correct server-side fail-closed admission |

## Root cause

Run2r did not include `contract_coverage` in the first `publish_plan` evidence
object at all. It was not nested under the wrong property, dropped by JSON
serialization, or rejected because of a schema representation mismatch. The
server received a structurally valid evidence object missing the planner's
mandatory coverage relation and rejected it.

## Comparison with run2p

Run2p contained a coverage array but used reconstructed item references. After
the trusted-scope correction, the model stopped guessing those references but
over-corrected by omitting the coverage member entirely. Thus the fix removed
the unsafe guessed-reference behavior without establishing a reliable
renderer-to-publication mapping step.

## Production-layer recommendation

The planner dispatch renderer should expose a direct immutable mapping from the
server-issued planning catalogue to the publication evidence skeleton, while
leaving the model responsible for writing status and verification. The
publication boundary should continue to require one exact coverage entry for
every current planner item and reject omission, stale references, duplicates,
and reconstructed references.
