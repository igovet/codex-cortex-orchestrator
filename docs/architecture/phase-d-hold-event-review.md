# Phase D hold and event-journal architecture review

Review date: 2026-08-29  
Reviewer: independent source reviewer  
Scope: Clarification Hold, family decision cutover, continuation renderer,
publication reconciliation, sanitized MCP event journal, live transport, v18
schema maintenance, package closure, and instruction-surface lint.

## Decision

**Source review clearance: granted.** The notification-form gap in
P1-HOLD-001 is corrected at the production boundary, direct stdio probes pass,
and the complete focused source clearance set is green. Candidate refresh and
live-dev remain intentionally unrun; they must not be represented as passed
until their separate gates run.

There is no P0 or remaining P1 finding in this source review. The hold,
renderer, publication, and journal design preserves the non-autonomous
coordinator boundary, and every public `tools/call` envelope—including
notifications—now has one observable terminal outcome.

## Evidence reviewed

The review inspected the current production sources, tests, and architecture
artifacts, including:

- `plugins/cortex/scripts/cortex_runtime/v12_store.py`
- `plugins/cortex/scripts/cortex_runtime/domain_kernel.py`
- `plugins/cortex/scripts/cortex_runtime/domain_api.py`
- `plugins/cortex/scripts/cortex_runtime/public_contracts.py`
- `plugins/cortex/scripts/cortex_runtime/semantic_registry.py`
- `plugins/cortex/scripts/cortex_runtime/mcp_api.py`
- `plugins/cortex/scripts/cortex_runtime/event_journal.py`
- `plugins/cortex/scripts/cortex_runtime/worker_message.py`
- `plugins/cortex/scripts/cortex_runtime/v12_maintenance.py`
- `scripts/cortex-live-smoke` and `scripts/cortex-live-capture-sink`
- `plugins/cortex/runtime-payload.json`
- the focused hold, journal, renderer, registry, maintenance, public-contract,
  and live-helper tests.

With temporary owner-only `HOME` and `CODEX_HOME` directories, explicit
`PYTHONPATH` for the installed test runner, `PYTHONDONTWRITEBYTECODE=1`, and
`python3 -B`, the focused pytest source suite passed:

```text
84 passed, 25 subtests passed in 3.62s
```

The suite covered clarification holds, event journaling, worker handoff,
public error registry, v17/v18 maintenance, first-call conformance, semantic
registry, domain-kernel receipts, decision aggregates, and live-helper docs.
Prompt lint and marketplace validation also passed:

```text
contract-lint: passed
marketplace validation passed
```

The isolated run used only source imports and temporary private directories; it
did not use a candidate package or live session. An additional direct stdio
probe sent an ID-bearing unknown-operation request, ID-less unknown-operation
and malformed-argument notifications, and a valid ID-less notification. It
observed request parity (one `-32602` response plus one sanitized event), no
responses for notifications, exactly one sanitized event for each malformed
notification, no retained operation name, and no event for the valid
notification. No candidate refresh, stable plugin update, tmux session, or
live-dev check was performed in this review.

## P1 findings

### P1-HOLD-001 — MCP composition does not journal every `tools/call` validation error

**Status: CLOSED at source boundary.**

The current request loop correctly routes object requests with IDs through
`finish_malformed_tool_call()` for non-object `params`, extra envelope
properties, missing operation selection, and non-object argument containers.
It also routes recognized schema, service, SQLite, internal, and physical-wire
failures through the terminal observation point. The focused regression covers
those four malformed request shapes and passes.

The original no-ID branch returned immediately after checking only extra
properties. A notification such as:

```json
{"jsonrpc":"2.0","method":"tools/call","params":{"name":"not_a_tool","arguments":{}}}
```

previously produced no journal file and no event. The same silent path applied
to a notification with a non-object argument container.

The no-ID `tools/call` branch now validates the operation name and argument
container before retaining the established no-dispatch/no-reply behavior for a
valid notification. An unknown operation or malformed arguments route through
the same `finish_malformed_tool_call(..., respond=False)` terminal observation
path as requests. There is no JSON-RPC reply, exactly one safe
`unknown`/`validation_error` event, and no caller payload is retained.
Black-box stdio tests cover unknown-operation and malformed-argument
notifications, assert no replies, and compare their event metadata to the
request form. A direct isolated probe independently confirmed the same result
and confirmed that a valid notification retains the intentional no-dispatch,
no-reply behavior.

### P1-HOLD-002 — Event-journal path validation does not close the ancestor chain

**Status: CLOSED at source boundary.**

The remediation now opens an already-existing owner-only `CODEX_HOME`
descriptor, then creates/opens the event root and session directory
descriptor-relative with no-follow flags and owner-only mode checks. The final
`events.jsonl` file is created with no-follow semantics, forced to mode 0600,
and checked as a regular file owned by the current user. Missing or unsafe
isolated roots become a non-blocking observation limitation and do not create
arbitrary paths.

`tests/test_mcp_event_journal.py` covers a mode fault and symlink substitution
for `CODEX_HOME`, the event root, the session directory, and the final file;
the focused test suite passed. The implementation therefore closes the
original gap from the isolated runtime root through the session directory.

### P1-HOLD-003 — Renderer leaks a canonical subject identifier into continuation text

**Status: CLOSED at source boundary.**

`render_clarification_continuation()` now requires exact typed compact task,
assignment, decision, and declared-subject anchors before rendering. Subject
projection maps the declared subject kind to its permitted compact reference
family and rejects missing, malformed, or wrong-kind values. The untrusted
continuation payload receives only the compact subject anchor; neither supplied
canonical subject form is copied into the message. The trusted continuation
policy contains semantic workflow guidance but no MCP property inventory.

Adversarial renderer tests cover missing anchors, wrong compact kinds,
canonical report-subject leakage, Unicode answer material, and the required
English continuation policy. The focused suite passed.

### P1-HOLD-004 — Oversized MCP reply can be journaled as success while the wire returns an error

**Status: CLOSED at source boundary.**

Recognized tool calls now use `finish_tool_call()`, which first performs the
complete physical JSONL reply check and only then emits the terminal event. If
the semantic result cannot fit, the client receives the standard bounded
JSON-RPC internal error and the event records exactly one `ledger_error`
failure. A semantically successful handler is therefore never reported as a
wire success when the client received an error.

`tests/test_mcp_event_journal.py::test_stdio_observes_wire_size_failure_once_not_handler_success`
asserts the response and journal outcome together; it passed in the isolated
source suite.

### P1-HOLD-005 — Native dispatch digest is persisted but absent from the hold delivery evidence

**Status: CLOSED at source boundary.**

Assignment-origin holds derive and persist a digest from the server-saved
assignment/native task relation. The public closed `host_delivery` projection
now includes that bounded non-callable digest beside compact assignment and
decision references, while retaining the exact saved native task name and
opaque continuation capability. The store independently recomputes the digest
before projecting or claiming delivery and fails closed on tampering.

Focused hold tests prove digest parity, public projection presence, replay
parity, and rejection of a fixture-only digest mutation before a host claim.
The first accepted publication by the same assignment reconciles pending
delivery in its own publication transaction; no backend path schedules or
fabricates a worker, and an unavailable/no-publication hold remains pending or
unavailable as recorded.

### P1-HOLD-006 — Focused source gate is red due an unregistered `ok` token

**Status: CLOSED at source boundary.**

The journal vocabulary now uses `outcome` and optional `fault`, not an event
`code` field or `safe_code="ok"`. The registry scanner therefore examines real
public error construction without mistaking an observation status for an
unregistered public error. `tests/test_phase_d_candidate_root_cause.py`
passed as part of the 84-test focused run.

## Confirmed architectural properties

The re-review also confirmed the following properties from source and focused
tests:

- v18 is a forward-only, complete hold migration after v17; bootstrap and
  maintenance fail closed on malformed nominal-v18 state.
- Binding issuance and hold creation share the decision command's
  `BEGIN IMMEDIATE` transaction. Recording the answer and consuming the hold
  share one transaction. Repeated opens converge on one binding and concurrent
  records produce one decision plus a replay.
- The public catalogue is a single fixed semantic registry. Tool descriptions
  carry the open-before-question and record-before-continuation sequencing;
  skills, profiles, and live workload prompts do not carry MCP argument
  recipes. Prompt lint passed.
- Publication reconciliation is bounded to the exact assignment's first
  accepted publication, is transactionally replay-safe, and never schedules a
  worker. Host delivery is a closed projection, not a fake host adapter.
- The continuation renderer has a version, profile state/digest, compact
  anchors, and a trusted English policy. Canonical IDs and MCP schema/property
  inventories are excluded from its host-facing message.
- The event journal is owner-only, bounded, append-locked, sanitized, and
  non-blocking. It fingerprints task/assignment anchors and omits raw
  arguments, references, prompts, capabilities, paths, and diagnostics.
- `scripts/cortex-live-smoke` remains transport-only: exact-session output and
  event observation do not parse readiness, approval, acceptance, replay, or
  MCP error semantics. `runtime-payload.json` includes the new runtime modules.

## Remaining release gates

Source clearance is complete. The remaining release gates are external to this
source review:

1. run the pytest-only exact-candidate suite from the staged content-addressed
   package with no checkout imports; and
2. run the focused LLM-driven live-dev decision smoke through the real ordinary
   Codex/tmux session, with the LLM inspecting both pane output and the exact
   sanitized worker event stream.

Candidate/live checks must not be represented as passed merely because source
tests are green. Update the qualification and parity matrices only from the
observed candidate/live evidence after those gates run.
