# MCP live event journal

Status: implemented source boundary; candidate and live qualification remain
separate gates.

## Purpose and boundary

Native worker MCP calls can be hidden from the coordinator pane. The runtime
therefore emits one owner-only bounded observation record for every public MCP
tool result or tool error at the stdio composition boundary. This journal is
not a ledger, an audit authority, a scheduling channel, or an acceptance
engine. The LLM reads it together with the real terminal and decides whether a
run is clean.

The live transport creates a fresh exact-session journal below the isolated
Codex runtime and exposes it through `cortex-live-smoke events`. The command
only prints a bounded stream. It does not parse readiness, infer success,
answer a product question, approve a plan, retry a call, or choose cleanup.

After the server has physically selected a successful MCP `initialize`
response, it also emits one passive `server_ready` registration observation for
that process. It contains only the verified build identity plus a count and
one-way digest of the advertised catalogue. It is not a tool result, does not
contain a tool name or definition, and is not emitted when initialization
falls back at the JSONL wire boundary. The packaged runtime derives and claims
its owner-only generation from its verified isolated candidate location; it
does not depend on a shell-provided journal path or inherited environment.

## Safe record model

Each JSONL record has a monotonic sequence and monotonic timestamp; semantic
operation; command/query kind; an internal `outcome` (`success` or `failure`)
and, only for a failure, a registry-safe `fault`; command mutation
classification (`new`, `replay`, `conflict`, or `error`); a coordinator or
assignment scope when that can be proved from the semantic anchor; fingerprints
of present task/assignment anchors; publication type/status when present; and
the verified runtime build identity. `outcome` is deliberately not a public
tool-error code: the public registry remains the only error-code authority.

The `server_ready` record is the narrow registration variant: its operation is
`server_ready`, its kind is `registration`, and it has no mutation, tool
result, request anchor, or fault. Its safe fields are limited to `build_id`,
`catalogue_count`, and `catalogue_digest`; the digest is not a printable list
of operation names or schemas.

The journal never records raw task or assignment references, request arguments,
responses, user text, prompts, reports, native task names, continuation
capabilities, secrets, diagnostics, project paths, or host messages.

## Filesystem policy

The journal and its containing session directory are private regular
owner-owned objects. The runtime opens the existing isolated `CODEX_HOME` root
without following links, then opens/creates the journal root and session
directory descriptor-relative from that verified root. It rejects symlinks and
incorrect ownership/modes at every isolated-runtime ancestor, bounds retention
to 512 complete records and 65,536 bytes, serializes writers with an exclusive
file lock, and rewrites only complete JSONL tails. An observation write failure
does not modify the canonical mutation result. It is an observation limitation
that the live helper reports without turning the MCP result into a fake tool
failure.

The terminal observation is emitted only after the physical JSONL reply
boundary chooses its response. If a valid semantic result exceeds the physical
wire limit and the client receives the standard internal response instead, the
journal records one `failure` with the safe ledger fault—not a false success.

Malformed `tools/call` notifications receive no JSON-RPC response, as required
by the protocol, but they are not invisible to verification: an unknown
operation or malformed argument container produces one sanitized terminal
failure observation through the same boundary as the request form. Valid
non-tools notifications and valid no-dispatch tool notifications retain their
ordinary no-reply behavior.

## Acceptance use

For an orchestration run with native workers, the LLM verifier inspects the
bounded journal after each material worker stage. It must find no prior hidden
validation/tool error or unexplained mutation replay, and it must observe the
first successful assignment-scoped worker-owned publication. A report
reference alone is insufficient. The journal remains evidence for the LLM,
not a rule engine.
