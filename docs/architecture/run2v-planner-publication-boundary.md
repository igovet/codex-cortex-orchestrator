# run2v planner first-call publication boundary

Historical development analysis. Cortex 1.14.10 no longer renders or accepts a
bearer bootstrap capability. The exact server-rendered native message carries
only the worker-scoped locator; a supported-host digest receipt plus the first
terminal assignment read binds the monotonic worker connection. The historical
text below explains the earlier failure and is not a current runtime contract.

## Finding

The failure is not a reason to relax publication admission. The server already
derives the planner's complete current scope and `consume_assignment_evidence`
returns it as authoritative structured content. The weak boundary is the
model-facing output contract: the advertised `effective_contract` projection
currently describes `planning_items` and `assigned_items` only as generic
arrays. It does not describe the item object shape or make the byte-exact
reference and one-to-one coverage relation locally visible at the point where
the worker receives its authority.

That leaves two different representations in the same flow:

1. the real consumed result contains item objects with compact references,
   category, ordinal, and text; and
2. the advertised result schema exposes an untyped array, while publication
   admission requires exact coverage of the server-owned item set.

The server therefore has the authoritative data, but the worker-facing
contract does not mechanically expose enough structure to enumerate and map
that data reliably on its first publication. A retry that happens to copy a
reference from another rendering is not an architectural correction.

## Root correction

Use one shared closed scope-item schema for every server-owned planner and
worker scope projection. The schema must describe the compact reference,
semantic category, ordinal, and bounded text, and must state that the compact
reference is copied byte-for-byte from this result. The same typed item schema
must be used by the assignment brief and by the successful bootstrap-consume
result; neither path may expose an untyped array or a host-reconstructed scope.

The consume result remains the publication authority. A native worker may start
with only the exact assignment locator and opaque bootstrap capability, consume
the server-issued evidence, and derive its complete publication coverage from
that returned scope. The rendered message remains bootstrap context and must
not be treated as a second scope authority. The backend continues to reject
omission, duplication, substitution, stale-revision, cross-assignment, and
extra references.

The publication contract no longer makes workers echo the coverage universe as
an authoritative evidence field. An optional annotation may be present for
human readability, but it is ignored for admission. In the same atomic
publication transaction the server derives the active universe from the task,
assignment, and effective-contract revision, then stores one coverage row per
item. Completed terminal results derive `complete`; partial derives `partial`;
blocked and failed derive non-complete `unverified` states. Planner reports
record planned treatment separately and do not impersonate delivery ownership.
This is a server-side authority correction, not a prompt workaround or relaxed
validation.

## Required evidence

The regression must use the real stdio boundary: list the advertised schemas,
open a planner assignment, deliver only the exact server-issued bootstrap
anchors, consume the assignment, and build the first publication solely from
the structured consumed scope. It must prove that the typed scope items and
their references are present in the advertised result and that the first
publication succeeds. Separate negative cases must omit, duplicate, alter,
cross, and stale a reference and must receive the existing precise backend
coverage error without mutation.

No model-facing skill, prompt, or repository policy should contain MCP field
names, request shapes, or payload examples. If a first call requires such a
hint, the advertised contract remains defective.
