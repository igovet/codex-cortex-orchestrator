# Optional Advisory Lane

The Advisory Lane is an optional, coordinator-owned conceptual pre-check for a
potentially expensive assignment. It can help sharpen a hypothesis, compare
conceptual approaches, identify a blind spot, or suggest a discriminating check.
It is not a worker profile, pipeline stage, runtime dependency, routing rule, gate,
or acceptance mechanism, and it never replaces project evidence or a bounded
worker.

## Strict availability and absence behavior

The coordinator checks only the live host tool catalogue. The lane is eligible
only when one catalogue entry has an explicit provider/server identity that names
`mcp-rubber-duck` and the exact tool name `ask_duck`. Availability must not be
inferred from a static manifest, package name, configuration, cached assumption,
or a generic host-equivalent name.

When that MCP is not exposed, or the entry is unrelated or identity-less, the
lane is a silent no-op. No consultation logic runs, and there is no warning,
blocker, degraded state, routing change, pipeline mutation, or user-facing event.
Existing Cortex routing continues unchanged. This documentation does not claim
that `mcp-rubber-duck` is installed or that its provider advice is effective.

When available, the coordinator inspects the live server/tool declaration and any
provider or model metadata exposed by that MCP, then selects the configured
provider/model appropriate to the bounded question and budget. Provider choice is
never inferred from package names, static configuration, cached assumptions, or
an invented schema. Missing, malformed, or ambiguous metadata is not safely
selectable and keeps the lane a silent no-op.

## Bounded consultation

The implementation recognizes only the live, identity-verified qualified host
name `mcp__rubber_duck__ask_duck` (a bare `ask_duck` never qualifies). At
consequential, contradictory-evidence, and repeated-failure transitions the
coordinator records `consider_advisory_lane` as `consult` or `skip(reason)`.
Calls are optional, bounded, and deduplicated by a privacy-safe packet hash;
`prompt` is the only required field, while provider/model/temperature/images
remain optional. Absence, errors, and timeouts are quiet diagnostics.

If the exact capability is exposed and the coordinator chooses to consult it,
there is one bounded call with one compact packet: the question and decision
boundary, desired outcome, constraints, verified artifact/report versions,
relevant facts and hypotheses, attempted approaches, and the next discriminating
check. The packet excludes secrets and unnecessary repository content.

The consultation cannot read the repository, edit files, run commands or tests,
perform verification, accept an outcome, spawn agents, or own an incident. The
coordinator retains causal ownership and decides whether to assign a real worker.
Escalate whenever repository discovery, mutation, execution, verification,
acceptance, or consequential architecture, security, migration, or public-contract
judgment is required. Budgets remain bounded per decision and task; V1 has no
councils, debates, voting, conversation fan-out, provider bridge, or recursive
worker route.

See the [senior consultant protocol](../senior-consultant/index.md) for the
separate report-only consultation route and its evidence boundaries.
