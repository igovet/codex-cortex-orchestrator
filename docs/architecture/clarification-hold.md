# Clarification Hold aggregate

Status: Phase D implementation in progress. This document is the durable
architecture contract for the worker-continuation bridge exposed by the
clarification decision family. It preserves the public fifteen-operation MCP
catalogue and does not make the backend a scheduler.

## Problem solved

A decision binding identifies a question and one response, but by itself it
does not prove that the rendered question is durable, that an assignment is
waiting for it, or that the recorded response must reach the same native
worker. A `Clarification Hold` adds that missing causal relation.

## Lifecycle

| Transition | Durable result | Backend boundary |
| --- | --- | --- |
| Open clarification | One server-owned binding and one `pending_question` hold, replayed for identical logical intent | Creates/replays before the coordinator renders the question; does not render or author it. |
| Record clarification, coordinator-only hold | One immutable decision and `coordinator_completed` | Does not create a worker or a delivery request. |
| Record clarification, assignment-origin hold | One immutable decision and `pending_delivery` relation | Does not wake, choose, or schedule a worker. |
| Supported host adapter | Not available in the Codex V12 plugin ABI | The package intentionally rejects lifecycle hooks and exposes no safe collaboration-follow-up result callback. Cortex never fabricates a claim or completion. |
| Exact assignment publication reconciliation | `pending_delivery` → `delivered` in the first accepted publication transaction from the saved assignment | The report is durable same-worker continuation evidence. The backend neither sends a message nor schedules/resumes work. Replays do not make another transition. |
| No publication / unavailable host observation | Remains `pending_delivery` unless a real supported private adapter records `unavailable` | This is evidence for coordinator-owned parent-linked recovery, never authority to create a replacement automatically. |
| Coordinator recovery | Existing parent-linked recovery policy | A coordinator may decide on recovery only from durable pending/unavailable evidence; the ledger never selects a replacement. |

The terminal worker publication remains worker-owned. A hold has no terminal
report and cannot falsely claim that a worker has continued.

## Identity and authority

The `open_clarification` command saves the optional originating assignment
relation from the already-resolved assignment reference. The store derives the
native dispatch identity from the saved assignment, calculates an immutable
native-dispatch digest, and mints a random continuation capability. The public
record result carries the exact compact assignment relation, exact saved native
task name, non-callable native-dispatch digest, response decision evidence,
opaque capability, and a trusted renderer-owned continuation message. The
coordinator passes that message unchanged to the host; it does not reconstruct
an identity or a message. Consumers can independently compare the returned
digest against the persisted assignment/native-task relation; a mismatch fails
closed as ledger corruption before a host delivery may be claimed.

The continuation renderer accepts only exact compact task, assignment, and
decision anchors. It derives the decision subject's compact anchor from its
declared subject kind and rejects absent, malformed, or wrong-kind anchors.
Canonical internal identifiers are never copied into the worker-facing message
or its untrusted JSON payload.

The capability remains accepted only by private host-facing store APIs. They
check the exact hold, host identity, and state in one transaction. They are not
MCP tools, are not referenced by skills/prompts, and do not provide a way for
the backend to call host APIs itself. The current packaged ABI has no such host
callback, so these APIs remain private capability seams rather than a claimed
runtime integration.

## State and replay rules

The primary states are `pending_question`, `pending_delivery`, `delivered`,
`coordinator_completed`, and `unavailable`. `delivery_claimed` is a bounded
host-adapter in-flight state that makes an exact host claim replay-safe. The
historical `stale` and `superseded` states remain reserved for a later contract
revision lifecycle; a stale record does not create a replacement binding.

Identical open requests converge on the same binding/hold. Identical record
requests return the original immutable decision/hold state. A changed response,
wrong family, stale relation, different project, different host identity, or
different host outcome conflicts precisely. Process restart reconciliation
reads the same durable rows; it never opens a second hold.

## Verification matrix

| Property | Source evidence | Exact-candidate stdio evidence | Live evidence |
| --- | --- | --- | --- |
| Coordinator-only lifecycle | `tests/test_clarification_holds.py` | Required | Required only when user clarification is coordinator-originated. |
| Assignment exact identity | `tests/test_clarification_holds.py` | Required | Required. |
| Atomic duplicate/concurrent open/record | `tests/test_clarification_holds.py` | Required | Observed as one open and one record only. |
| Lost response/restart/cross-project | `tests/test_clarification_holds.py` plus decision qualification | Required | N/A. |
| Private host claim/delivered/unavailable replay | `tests/test_clarification_holds.py` | Required if a supported host adapter is added | Not applicable until that ABI exists. |
| First same-assignment publication reconciliation | `tests/test_clarification_holds.py` | Required | Required: the LLM verifies the first worker-owned publication event, rather than inferring continuation from a final reference. |
| Renderer-owned continuation | Contract/renderer tests | Required | Pane message and host follow-up must match the returned projection. |

No row is a live pass until the isolated candidate is provenance-qualified and
the LLM observes the coordinator pane plus the bounded worker event stream.
