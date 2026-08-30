# Official MCP protocol audit

Status: architecture review, 2026-08-29  
Scope: the isolated Cortex `1.12.1` candidate and its current stdio server.  
Decision rule: this document identifies protocol gaps; it does not authorize a
compatibility layer or a second public API. Backward compatibility is
explicitly excluded unless the product owner requests it.

## Executive conclusion

The semantic architecture is compatible with MCP's core model: Cortex exposes
model-controlled tools, keeps domain truth in a durable server-side aggregate,
and returns typed `structuredContent` receipts. The durable task/assignment/
decision/publication relations must remain Cortex domain state; they are not
interchangeable with an MCP transport task ID.

The initial audit found three material protocol gaps. The first two are now
implemented in the source stdio transport; the third remains intentionally
capability-gated:

1. **Resolved in source — structured-result interoperability.** The server
   now returns
   `structuredContent` but intentionally does not also put its serialized JSON
   in a `TextContent` block. The official tools specification says a tool that
   returns structured content SHOULD also return serialized JSON in text for
   backwards compatibility. This is not required for a conforming modern
   client, but it is a real interoperability risk and must be an explicit
   release decision.
2. **Resolved in source — protocol version negotiation.** The server now
   declares and negotiates the core versions `2025-11-25` and `2025-06-18`.
   It returns the requested version when supported and the newest supported
   core version otherwise. The client remains responsible for disconnecting
   when it cannot accept the counter-offer.
3. **P1 — capability advertisement and enforcement for optional extensions.**
   The server advertises only `tools`, which is correct for its current core
   implementation, but it must reject/ignore task-augmented requests according
   to negotiated capability and must not imply that clarification or worker
   execution is an MCP Tasks feature. If MCP Tasks or elicitation is adopted,
   this needs a deliberate capability-gated vertical slice, not a field added
   to the existing tool schemas.

The current absence of ToolAnnotations, logging, progress, cancellation,
sampling, resources, and MCP Tasks is not by itself a defect: these are optional
features. They become defects only if the implementation advertises or relies
on them without implementing their lifecycle and capability negotiation.

## Official sources

The audit uses primary MCP documentation only:

- [MCP lifecycle, protocol/version/capability negotiation](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle)
- [MCP tools (listing, calls, results, output schemas, errors)](https://modelcontextprotocol.io/specification/2025-06-18/server/tools)
- [MCP schema reference](https://modelcontextprotocol.io/specification/2025-06-18/schema)
- [MCP elicitation](https://modelcontextprotocol.io/specification/draft/client/elicitation)
- [MCP Tasks (2025-11-25 experimental extension)](https://modelcontextprotocol.io/specification/2025-11-25/basic/utilities/tasks)

The Tasks page explicitly says that Tasks were introduced in 2025-11-25 and
remain experimental. The draft elicitation page also requires an originating
client request and capability declaration; neither feature is an implicit
replacement for an application domain protocol.

## Core protocol matrix

| Area | Official requirement or guidance | Current Cortex behavior | Severity / action |
|---|---|---|---|
| Transport | JSON-RPC MCP lifecycle over stdio; initialization precedes normal operations | `mcp_api.serve_stdio` has explicit `new → initialize_response_sent → ready` states and JSONL frame bounds | Pass; retain tests |
| Initialization | Client sends `initialize`; server responds with protocol version, capabilities, and server info; client sends `notifications/initialized` | Implemented; server info adds candidate provenance metadata | Pass for current version; add version-set negotiation (P1) |
| Version negotiation | Echo a supported requested version, otherwise select another supported version; peer may disconnect if unsupported | Explicit supported set `2025-11-25`, `2025-06-18`; requested version is echoed when supported, otherwise newest is counter-offered | Resolved in source; candidate black-box gate remains |
| Server capabilities | Tools servers MUST declare `tools`; optional capabilities must be negotiated | Emits `{"tools": {}}`; no optional extension is advertised | Pass; do not advertise unimplemented extensions |
| `tools/list` | Returns tools and supports opaque pagination cursor; list-change notification is optional | Fixed catalogue with opaque cursor handling; no list-change capability | Pass for immutable catalogue |
| `tools/call` envelope | `name` and optional arguments; unknown/malformed protocol requests are protocol errors | Validates envelope and maps correctable/business failures to tool results | Pass in principle; add conformance cases for malformed notifications and unknown tools |
| Input schema | Tool has object `inputSchema`; descriptions help the model understand calls | Closed per-tool schemas, descriptions, JSON Schema draft marker, model derives arguments from advertisement | Pass; preserve “no parameter recipes in prompts/skills” invariant |
| Output schema | If advertised, server structured result MUST conform; clients SHOULD validate | Server validates every successful projected result against advertised output schema | Pass; keep output schemas authoritative |
| Structured results | `structuredContent` is a JSON object; official tools guidance says also return serialized JSON text for backwards compatibility | Every successful result now contains deterministic serialized projected JSON as the first `TextContent` block plus the compact handle guidance block; both derive from the same value | Resolved in source; bounded reply fallback remains a gate |
| Tool annotations | Optional hints: title/readOnly/destructive/idempotent/openWorld; clients must treat them as untrusted | Not advertised | Pass; optional P2 metadata only after semantic review; never use it for authorization |
| Error split | Protocol errors for unknown tool/invalid protocol; tool execution errors in result with `isError: true` | Caller validation and service/ledger errors are returned as structured tool errors; lifecycle/envelope faults use JSON-RPC errors | Pass; verify no raw exception reaches stdout |
| Pagination | Cursors are opaque and requestors should reuse them | `tools/list` and bounded read projections expose cursor behavior | Pass; add cross-parameter cursor mismatch tests where applicable |
| Security | Validate inputs, access-control, rate-limit, sanitize outputs, timeouts/logging recommended | Closed schemas, bounded frames/results, isolated candidate provenance, sanitized observation journal | Pass with operational gates; rate limiting is a future deployment concern for stdio |
| Shutdown | Stdio client closes input, waits, then terminates if needed | Live helper owns exact-session cleanup; MCP process exits with launcher | Pass operationally; add direct stdio shutdown regression if needed |

## All 15 Cortex tools

The public catalogue is exactly the following, grouped by semantic role. The
same MCP contract applies to every row: the model must use only the advertised
schema and descriptions; no skill, prompt, or live workload may teach a call
shape.

| Tool | MCP classification | Idempotency/domain invariant | Protocol finding |
|---|---|---|---|
| `open_task` | command | One task contract per logical request; exact retry replays | Core MCP-compatible; server-owned identity remains domain state |
| `read_task` | query | Bounded task projection and chronology | Core MCP-compatible; cursor remains opaque |
| `open_clarification` | command | One pending binding per logical question/subject/assignment | Core MCP-compatible; candidate for elicitation adapter, not replacement |
| `record_clarification` | command | One immutable response; exact replay only; host delivery is explicit | Core MCP-compatible; delivery relation must remain durable Cortex state |
| `open_plan_review` | command | Server-owned approval binding tied to immutable plan view | Core MCP-compatible; human-in-loop remains host concern |
| `record_plan_review` | command | One decision against exact binding; conflict/stale states explicit | Core MCP-compatible |
| `open_steering` | command | One pending steering binding; same-task supersession rules | Core MCP-compatible |
| `record_steering` | command | Atomic decision plus effective-contract revision | Core MCP-compatible |
| `open_assignment` | command | Assignment identity, evidence declarations, model/profile choice | Core MCP-compatible; native worker task is not an MCP Task |
| `consume_assignment_evidence` | query | Read only declared evidence; cursor scoped to assignment | Core MCP-compatible |
| `publish_plan` | command | Atomic immutable plan publication and approval relation | Core MCP-compatible; output schema is useful here |
| `publish_result` | command | One terminal result slot per assignment; correction uses a new assignment | Core MCP-compatible; do not treat replay as automatic success |
| `publish_documentation` | command | Atomic documentation-impact publication | Core MCP-compatible |
| `assess_governance` | command | Advisory governance/initiative materiality; nonblocking | Core MCP-compatible; not an MCP progress/status channel |
| `close_task` | command | Closure only from complete evidence and resolved decisions | Core MCP-compatible; durable closure is not `tasks/result` |

## Clarification: elicitation versus the Cortex hold

MCP elicitation is a host interaction primitive. It requires the client to
advertise elicitation capability and requires the server request to be
associated with an originating client request. The response has `accept`,
`decline`, and `cancel` actions and is suitable for user input that the host
can present and validate.

Cortex `open_clarification`/`record_clarification` solve a different problem:
they provide a durable, idempotent, task/assignment-bound business decision,
with replay, conflict, stale-binding, worker continuation, and audit evidence.
Replacing that state with an elicitation response would reintroduce the exact
architecture defect this stabilization is fixing: the host answer could be
delivered to chat but not to the owning worker and could not be reconciled
deterministically.

Recommendation: keep the durable Cortex hold as the source of truth. Add an
optional MCP elicitation adapter only when the host advertises the capability:

```text
open_clarification (durable hold)
        │
        ├─ host supports elicitation → elicitation/create, correlated to call
        │                              → record_clarification (one answer)
        └─ host does not → ordinary host question → record_clarification
```

The adapter must persist the Cortex binding before eliciting, map all three
elicitation actions to explicit domain outcomes, reject unsupported modes, and
never create a second binding on transport uncertainty. Form mode must not be
used for secrets; URL mode is required for sensitive credentials under the
official guidance. No elicitation parameter recipe belongs in a skill or
prompt.

## Long-running work: MCP Tasks versus native workers

MCP Tasks are an experimental requestor-driven mechanism for a receiver to
accept a task-augmented request, return a receiver-generated task ID, and make
the eventual result available through `tasks/get`/`tasks/result`. They require
capability negotiation and tool-level task support. They also define
`input_required`, cancellation, TTL, polling, and related-task metadata.

The Cortex native worker lifecycle is broader than one MCP request: it includes
assignment creation, host dispatch, clarification, evidence consumption,
immutable publication, independent verification, rework, documentation
impact, and closure. Therefore the current architecture should not wrap the
whole orchestration in one MCP Task. The durable Cortex assignment and event
relations remain the system of record.

Potential future use: expose MCP Tasks only for a genuinely long-running,
single-request server operation (for example, a bounded report assembly or a
server-side verification job) after the host supports the full experimental
surface. It must not be used as a second identity system. The MCP task ID may
correlate to a Cortex assignment/command receipt, but it cannot replace the
server-issued Cortex refs, binding, idempotency key, or evidence relation.

## Optional features intentionally not implemented

- **Progress/cancellation:** useful for a single long-running request, but not
  a substitute for the assignment state machine. If added, implement request
  correlation, bounded progress, cancellation notification handling, and a
  maximum timeout. A cancelled MCP request must not silently cancel a durable
  Cortex assignment or publication.
- **Logging:** the sanitized event journal is local verification telemetry,
  not MCP `notifications/message`. Adding MCP logging would require a declared
  logging capability and client-controlled level behavior. Do not leak refs,
  prompts, report content, or credentials.
- **Sampling/tool choice:** Cortex delegates model choice to the host/coordinator
  and stores a selected profile/model as assignment evidence. It should not
  call back into sampling unless the host explicitly negotiates and the product
  defines authority boundaries.
- **Resources/prompts:** not needed for the fixed semantic catalogue. Adding
  them would require capability advertisement and a separate security review.
- **Tool annotations:** may improve UI but remain hints, never authorization,
  acceptance, or idempotency enforcement. The server must continue enforcing
  those properties in domain state.

## Required follow-up gates

Before release qualification, add or confirm tests for:

1. initialize with a supported requested version, an unsupported requested
   version with a supported fallback, and a no-common-version failure;
2. every advertised output schema against every successful tool result,
   including exact replay and typed error results;
3. structured-content compatibility policy (serialized TextContent present or
   an explicit modern-client-only release decision);
4. tools/list pagination, stable catalogue identity, and absence/presence of
   list-changed notifications matching capabilities;
5. no task augmentation accepted unless task capabilities and tool-level
   support are both advertised;
6. clarification hold replay, stale/conflict behavior, elicitation capability
   fallback, and exactly-once record/delivery reconciliation;
7. MCP cancellation/timeout behavior, if and only if the corresponding
   optional feature is implemented;
8. live-dev verification proving the first worker publication has no hidden
   MCP validation error or unexplained replay.

These gates preserve the complete orchestration feature set. They improve the
transport boundary without moving lifecycle authority out of the Cortex
domain kernel or teaching MCP call parameters through model instructions.
