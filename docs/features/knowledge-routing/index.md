# Knowledge routing and commands

For structural code discovery workers use available Codebase Memory before broad
source reading or filesystem symbol searches. A named file/function still requires
graph lookup when its implementation is unknown; only retained current source or
purely non-code edits avoid that discovery. Match the exact canonical root from
`list_projects`; when duplicate indexes exist, choose by health and relevant coverage
using `index_status`, never a guessed project name. Retain the selection within an
assignment. Initial missing indexes can be built locally for that workspace; watched
indexes normally update automatically.

The complete shared worker skill names `codebase_memory` and its discovery route.
Cortex report-tool discovery and Codebase Memory discovery are separate: filtering
the catalogue to Cortex names cannot establish that the graph provider is missing.
Detailed selection rules live in the declared code-and-evidence reference. A ready
matching index still needs relevant coverage when duplicate roots exist; status alone
does not reveal whether the assigned subsystem is excluded. Reports identify the
selected index/workspace or the concrete reason for source fallback.

Use `search_graph` for definitions, `trace_path` for relationships and `get_code_snippet`
for selected current source. Use scoped architecture or schema-grounded queries for
questions those operations cannot answer. Literal text and documentation may use
`search_code` or repository-native search. Exact argument contracts belong only in
the tools' live schemas, not in profiles.

Check coverage for cited/edited paths and scopes behind absence/completeness claims.
Handle pagination and omitted tests explicitly. Empty results and clean best-effort
coverage do not prove absence or completeness. Record any stale, partial, excluded or
unavailable evidence and use targeted source fallback. Never repeat a known-sufficient
search or reindex merely for reassurance. Current source and checks outrank graph edges.

This route was checked against the live MCP descriptions and the
[upstream documentation](https://github.com/DeusData/codebase-memory-mcp) on 2026-09-05.

The coordinator reads necessary user sources, pipeline state and as many selected
evidence pages as a decision requires. Previews guide selection, rather than imposing
a first-page-only rule. Workers load their assigned complete skills, inspect relevant
indexes and source, and return findings with verification and limits. Current source,
tests and executable configuration outrank generated docs.

Harvest preserves the canonical five project knowledge files and feature index.
Features use a directory with `index.md` as entry point; large features can split
into focused pages. A source-backed coverage matrix maps runtime owners and
entry points to documentation and verification, with totals, exclusions, unmapped
surfaces and unknowns. Manual text outside generated blocks is preserved.

Harvest maintains a complete baseline; refresh rebuilds inventory without trusting
old coverage, independently reviews completeness and performs a second no-change
planning comparison. These are documentation acceptance criteria, not server
stages or required Markdown report headings. Help is read-only. Clear delegates
bounded retention deletion to the installed maintenance command without opening
a new task. Normal leaves coordination without deleting documents.

After summarization the coordinator restores host-supplied rules, previews and the
current pipeline. Workers restore their assigned specialist skills and reread needed
reports and indexed documentation. The native thread binding and current pipeline stay stable.

## Profile routing and skill loading

The orchestrator skill contains a routing table for all 22 specialists. Each has
an exact `cortex:worker-*` skill token. The coordinator supplies that token and a
self-contained assignment through ordinary native subagent tools. The worker loads
its complete advertised skill before tool discovery or project work. Each assignment
leads with the exact worker-skill token and this short loading requirement; a generic
role or product brief does not transfer the shared protocol. Already attached
live schemas can be used directly, without a separate MCP catalogue bootstrap or a
prescribed first-call batch. This does not require custom native profile selection or
personal agent TOML registration.
The coordinator avoids loading a worker's full protocol without a concrete need.

Each structured profile names responsibility, inputs, workflow, checks and recovery.
The compact shared protocol is generated into every profile; rare procedures are
declared references loaded only when relevant. Conditional recovery and report-example skills load by exact ordinary skill
names only when needed. No installation paths are put into worker assignments.

The coordinator preserves its selected model and applies the worker route matrix.
Luna (`gpt-5.6-luna`) is the default/priority model for ordinary work and all
research, exploration and analysis assignments, at medium/high/xhigh/max effort.
Terra (`gpt-5.6-terra`) is reserved for explicitly complex work at those efforts.
Sol (`gpt-5.6-sol`) is limited to narrow security-analysis microtasks at
medium/high/xhigh; security implementation uses Luna or Terra and never Sol merely
because the subject is security. Reviews and verifications are stronger than their
implementation: Terra reviews Luna work, while Terra reviews Terra work at a
strictly higher permitted effort where available. Other models/efforts require an
explicit user override, which is preserved verbatim. Every assignment records the
model, effort and policy class. A timeout never transfers an active worker's
resources.

Current isolated live qualification uses Luna/high for all coordinators and Luna at
medium/high for native workers. That fixed evaluation setup does not change general
product routing.

Report examples for plans, investigations, implementations, verification,
documentation and final results are optional content guides. They do not impose
machine-interpreted headings or change the common publication operation. The
shared tool-discipline skill requires live declarations and complete requests,
with explicit error handling and no unexplained acknowledged mutation replays.

The coordinator preserves the language selected from the user’s own prose across
progress messages and recovery. Workers publish findings as ordinary reports;
pipeline and governance remain coordinator responsibilities. Worker progress,
questions, blockers and verification updates use only the native parent/subagent
channel; app task messaging such as `codex_app.send_message_to_thread` is forbidden
even when its destination is the coordinator. Final handoffs return automatically
through the native worker result. Code-mode calls
require preserving the intended argument through dispatch and checking the observed
result. Inert JavaScript literals, including `String.raw`, may carry a patch unchanged;
executable interpolation or substitution may not construct it.

Bounded changes may combine inspection, implementation, tests, and related docs in
one specialist assignment. Separate discovery and planning serve concrete unresolved
decisions; neither file counts nor acceptance-category counts impose fixed stages.
Bounded independent discovery may precede the first pipeline edition. Useful durable
state must be recorded before dependency, shared-resource or acceptance decisions;
dependencies still prevent implementation from racing governing investigations.

Ordinary marketplace installation and isolated dev preparation use the same
packaged skills without writing a personal agent registry. Missing custom profile
selection does not prevent delegation; unavailable native subagents or a missing
assigned skill remain real host limitations.
