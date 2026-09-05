# MCP contract review

## Sources and design

Reviewed against the official sources on 2026-09-04:

- [MCP tools, revision 2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25/server/tools):
  tool discovery, input/output schemas, structured results and error categories.
- [MCP lifecycle, revision 2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle):
  negotiate a revision that the server actually implements.
- [OpenAI: build an MCP server](https://developers.openai.com/plugins/build/mcp-server):
  focused operations, model-readable metadata and useful results without a UI.
- [OpenAI Codex: subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents):
  standalone Agent v2 TOML files are spawned as configuration layers and provide
  each custom agent's native `developer_instructions`.

Cortex serves one supported revision, 2025-11-25. It does not claim support for
newer revisions or retain an old protocol adapter. The client can accept the
server's proposed revision or disconnect. The seven tool definitions expose
English descriptions, exact input constraints, output schemas and safety hints.
The model receives data through tool results, not through hidden client metadata.
Successful structured data is also serialized as an identical text content item.
Skills contain execution policy rather than duplicated argument contracts.
Tool descriptions reinforce that coordinators read only catalogue previews and
the current pipeline; the native host attaches profiles and workers inspect full evidence. The server
keeps the same tools available to both roles without an actor-binding mechanism.

## Corrections made during review

The earlier server advertised 2024-11-05 despite returning structured content,
did not describe structured outputs with schemas, returned unknown tools as tool
execution errors, and exposed bare error codes. These were corrected together.
Parameter errors identify the affected field, a bounded description of the
received value, the accepted form and a concrete repair. Unsupported fields do
not echo their values, so accidental secrets are not repeated.
Unknown tools and malformed protocol envelopes use JSON-RPC errors. Tool input
and storage failures use visible error results. Storage failures explicitly warn
when delivery is uncertain; changing arguments is not presented as a disk repair.

A live native wrapper previously failed before MCP dispatch when report Markdown was
embedded in a JavaScript template literal. The only body-bearing operation is now the
built-in `apply_patch` file tool, using one escaped exact-marker hunk per call. Template
literals and `String.raw` are forbidden and audited. Report bodies and paths never cross
the writer call.
The draft creator allocates the correct project file, binds its short identifier to
the calling native thread, and places that identifier in the filename and Markdown.
The actor reads that exact draft through a bounded same-thread operation, updates the
original file in place, and publishes only the identifier with compact metadata.
Device and inode validation rejects deletion/recreation before publication.
Stdio and storage regressions verify code fences, quotes, backslashes, Unicode and
interpolation-like text survive publication and process restart unchanged.

## Large reports

The writer streams a complete server-created project draft in fixed blocks into a temporary file in
the task directory, validates UTF-8, calculates size and SHA-256, fsyncs it, and
atomically publishes the final report before committing metadata. All content is
retained verbatim with no application total-size cap, including reports larger than
16 MB. Pipeline editions use the same file-backed flow and can exceed the MCP request
limit. Exact retries recover their receipts after source deletion and reject changed
metadata or bytes. Disk exhaustion remains a real failure.

## Verification boundary

Schema validation covers the actual success results of all seven tools. Storage
tests exercise a file-backed report larger than 16 MB, a pipeline edition larger than
the MCP request limit, typed templates, thread-bound draft ownership, Unicode boundaries, catalogue
visibility, exact retries, restart, draft isolation, disk failure and
retention. Error tests
check useful corrections and absence of private rejected values. These are
source/stdio checks; actual model behavior in CLI and Desktop is recorded in
[release evidence](../release-readiness.md). Neither schemas nor instructions can
guarantee that every future model call will be correct.

## Native task routing: observed before implementation

Before changing task selection, a passive observer logged allowlisted metadata at
the server's actual MCP ingress on candidate `da94c9d187bf4cd8`. Ordinary CLI
0.153.0 and the actual Desktop each completed a read-only FAQ/source comparison
with one native explorer. Each host produced nine successful task operations;
coordinator calls carried their own thread with no parent, while each worker's
calls carried a distinct thread and the exact coordinator as parent. There were
no tool errors or mutation replays. The worker's first publication succeeded.
The CLI exited with status zero; the disposable Desktop was stopped after its
visible final result. Only thread linkage and operation/package metadata were
recorded, never complete transport objects or report content.

This agrees with the exact upstream Codex revision inspected:

- [MCP thread metadata insertion](https://github.com/openai/codex/blob/41e22fee981a63b3698df7ed36bad393cda24715/codex-rs/core/src/mcp_tool_call.rs#L1328).
- [Parent context and MCP projection](https://github.com/openai/codex/blob/41e22fee981a63b3698df7ed36bad393cda24715/codex-rs/core/src/turn_metadata.rs#L230).
- [Custom-server metadata regression](https://github.com/openai/codex/blob/41e22fee981a63b3698df7ed36bad393cda24715/codex-rs/core/src/mcp_tool_call_tests.rs#L1115).

Only after both real-host observations was automatic routing enabled. All seven
schemas omit task/thread selectors, and creation responses omit task identifiers.
Compact opaque cursors contain only a non-reversible task binding rather than the
internal task identifier. A changed pipeline returns its dedicated stale-cursor
correction; an altered cursor for an immutable report returns `invalid_cursor`.
Tool errors redact task identifiers from internal paths.
Missing host context, unknown parents and conflicting bindings have explicit safe
errors. Nested inheritance, restart, cross-task denial, concurrent creation and
retention are source-tested. Native routing does not authenticate a malicious
local MCP client and does not add worker assignment or semantic approval gates.

## Reliability follow-up — 2026-09-05

Publication now rejects unchanged guidance markers for every draft class, with the
same repairable draft and no accepted delivery receipt. All actors use one in-place
patch with independent marker hunks. Pipeline recovery preserves the committed
backup until digest reconciliation; rename rollback is registered before directory
sync. Fault-injection tests exercise actual process exit before database commit.

Real CLI, CLI resume and Desktop passed Cortex-specific audits on the same payload.
The live transport never resubmits an uncertain prompt automatically, and resumed
observation includes the existing task while excluding earlier calls. Project tool
diagnostics remain separate from Cortex report and coordination failures. Current
counts and limits are in [release evidence](../release-readiness.md).

Passive observation found an accepted task request translated and shortened by
the coordinator, losing an execution restriction. The first correction strengthened
the copied-request description, but did not make transcription reliable. This
intermediate approach was replaced by the native source capture described below.
The installed plugin and observed task are left untouched during observation.

A later worker mistyped a report identifier, received `invalid_arguments`, then
probed an already-read report before obtaining the correct reference. Identifier
error guidance and the read property now require an unchanged authoritative
reference and explicitly rule out character repair or probing another document.
The observed worker recovered through coordinator correction; no report was mutated.

Live stress testing later showed that copy instructions alone still allowed a
whole input-preservation sentence to disappear. Task creation now captures typed
native user input server-side and removes the model-authored request field from
its public schema. The server reads only the current thread/project source,
rejects unavailable input and applies explicitly requested literal credential
redactions. The output includes the saved source digest for content-free auditing.
Tests cover source isolation, missing input, symlinks, turn boundaries, exact
Unicode/whitespace preservation, literal redaction, rejected copied-request fields,
and idempotent replay after host source removal.

All seven tools accept optional literal credential redactions for newly captured
user text. Coordinator reads may atomically archive pending source messages, so
readOnlyHint is false. Operation receipts stay unchanged on replay; source reports
appear through the existing bounded catalogue and document reader. No eighth tool,
workflow hook or model-authored steering argument is introduced.

Report page reads answer a concrete missing fact. Tiny connectivity/reference
probes before publication are explicitly prohibited in the live property contract
and shared worker guidance; the audit retains duplicate immutable-start findings.
