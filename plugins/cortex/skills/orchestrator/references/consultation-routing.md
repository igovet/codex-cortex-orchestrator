# Optional consultation routing

Advisory Lane is an optional, coordinator-owned consultation heuristic before a
potentially expensive assignment. It is not a pipeline stage, worker profile,
runtime dependency, routing rule, gate, or acceptance condition. Use it only when
a short external opinion could materially reduce conceptual uncertainty, rework,
or an unnecessary assignment; otherwise continue directly.

## Capability and absence semantics

Detect this capability only from the tools exposed in the current live host
catalogue. V1 activates only when one catalogue entry has an explicit
mcp-rubber-duck provider/server identity (the identity must name
`mcp-rubber-duck`) and the tool name is exactly `ask_duck`. An unrelated
`ask_duck`-like tool, or a tool with no explicit mcp-rubber-duck identity, does
not qualify. Do not infer availability from a static manifest, package name,
configuration, cached assumption, or a generic host-equivalent name.

If no such tool is exposed, do not execute Advisory Lane logic at all. Continue
the current Cortex routing silently: absence is not a warning, blocker, degraded
state, evidence gap, pipeline mutation, or user-facing event. Cortex behavior and
its existing worker routing remain unchanged.

When the exact capability is exposed, inspect the live server/tool declaration
and any provider or model metadata it actually exposes. The coordinator chooses
the configured provider/model appropriate to the bounded question and budget;
never infer one from package names, static configuration, cached assumptions, or
an invented schema. Missing, malformed, or ambiguous provider/model metadata is
not safely selectable and therefore remains a silent no-op.

## V1 consultation contract

When `ask_duck` is actually exposed and the coordinator chooses consultation, make
one bounded call with one compact decision packet containing: the question and
decision boundary, desired outcome, constraints, verified artifact/report versions,
relevant facts and hypotheses, attempted approaches, and the next discriminating
check. Keep the packet free of secrets and unnecessary repository content.

The Duck may sharpen a hypothesis, compare conceptual approaches, identify blind
spots, suggest a discriminating check, or help decide whether a worker is warranted.
It never owns the incident or task, edits files, reads the repository, runs tests,
proves implementation correctness, performs verification, or accepts an outcome.
Escalate to a real bounded worker whenever project evidence, repository discovery,
mutation, execution, verification, or acceptance is required. Escalate consequential
architecture, security, migration, or public-contract decisions to the applicable
senior route.

Keep budgets bounded per decision and task; no councils, debates, multi-model
comparison, voting, conversation fan-out, or bridge is part of V1. Provider/model
choice remains limited to metadata exposed by the live capability and the concrete
question; it never creates a recursive worker route or changes Cortex routing.

The result is advisory context only. The coordinator retains causal ownership,
chooses assignments, interprets uncertainty, and makes every implementation,
verification, delivery, and acceptance decision.
