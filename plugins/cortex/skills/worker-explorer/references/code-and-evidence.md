# Code and evidence discovery

Read this reference only for structural repository discovery or when saved report
detail is needed.

Use the live `codebase_memory` contracts for structural discovery. Discover only the
needed provider-qualified operation names and read their complete advertised schemas;
a lookup restricted to Cortex operations does not establish graph-tool availability.

Match a code index to the canonical assignment workspace using the full root returned
by `list_projects`, not a similar name or another worktree. If multiple entries match, compare
`index_status` and relevant `check_index_coverage` results to select the healthy index
covering the assigned scope. A ready index can still exclude the entire subsystem.
Retain the selection within the assignment. If no matching index exists, initial
`index_repository` use is allowed for the authorized workspace; this does not authorize
changing ignore rules or indexing other projects.

Use the smallest operation that answers the question: `search_graph` for definitions,
`trace_path` for callers, dependencies and impact paths, and `get_code_snippet` for the
selected implementation. Use scoped `get_architecture` for orientation, or
`get_graph_schema` before a complex `query_graph`; `detect_changes` serves an
established Git comparison. Literal/configuration/documentation text may use
`search_code` or bounded native search. Follow live schemas for arguments.

Confirm consequential graph findings against current source. Check coverage for cited or
edited paths and scopes behind negative or exhaustive claims. Follow returned
pagination when the question requires more results, and account for omitted tests.
Empty results and clean best-effort coverage do not prove completeness. Stale,
skipped, excluded or unsupported files require bounded direct inspection.

If no matching healthy index is available, record the limitation and use one bounded
repository-native fallback for the concrete question. Do not loop on unavailable
graph tools, infer absence from no hits or rebuild an index after every small change.
Fresh source, executable configuration and observed tests outrank derived graph
relationships.

In code-investigation reports, identify the selected index and matching workspace,
or the concrete limitation that required source fallback. Merely listing available
tools is not evidence that graph discovery was performed.

Treat report references as opaque values supplied by the coordinator or a live
catalogue result. Read only reports relevant to the assignment. Begin each document
at no more than 4,000 characters and continue with its cursor until the needed fact
is found. Reuse unchanged pages. Record the source revision, artifact revision and
remaining coverage gaps in the published evidence.
