# Candidate-anchored observation-generation protocol

Status: source review in progress; this document does not claim a candidate or
live acceptance result.

## Purpose

The sanitized MCP observation stream is an operator/LLM observation channel,
not orchestration authority. It must be anchored to the exact installed
candidate selected by the isolated receipt and must never become a fallback
journal, a model-input channel, or an acceptance decision engine.

## State machine

| State | Owner | Entry condition | Legal successor | Forbidden outcome |
| --- | --- | --- | --- | --- |
| `absent` | none | No fresh request exists | `pending` | Any default or environment-selected journal path |
| `pending` | isolated launcher | Verified candidate receipt and catalogue identity create one owner-only signed request | `claimed`, `expired` | A request with stale time, mismatched build/version/catalogue/session, unsafe ownership/mode, or invalid signature |
| `claimed` | one candidate MCP process | Exact candidate verifies receipt and atomically moves the deterministic fresh request | `ready`, `limited` | A second process claiming the same request or a request from another candidate/session |
| `ready` | claimed MCP process | The initialize reply crossed the physical wire and ready metadata matches the claimed request | `observed`, `limited` | Ready metadata that differs in build/catalogue/generation/session |
| `observed` | transport | The exact smoke session reads its selected owner-only generation without interpreting events | `limited`, `cleaned` | Drift to a later generation, cross-session event selection, pass/fail judgment |
| `limited` | runtime/transport | Observation storage or safety validation fails | `cleaned` | Altering an MCP result, retrying a mutation, or asserting readiness/pass |
| `expired` / `cleaned` | launcher/transport | TTL expires or exact session cleanup completes | `absent` | Reusing stale generation state |

## Immutable identity relation

```text
isolated candidate receipt
  ├── exact candidate path / version / build
  └── exact isolated CODEX_HOME
          └── signed pending generation
                ├── build + candidate version
                ├── catalogue count + digest
                ├── session + generation ID
                └── claimed generation
                      ├── matching ready receipt
                      └── bounded owner-only events
```

Repository delivery metadata and the candidate cache are not interchangeable:
the receiver validates the receipt-selected installed plugin root and its
runtime-derived identity. The request and ready receipts contain only compact
identity metadata; they contain no prompts, MCP arguments, opaque handles,
reports, project paths, or private diagnostics.

## Observer receipt boundary

`cortex-live-smoke events` treats the receipt file as untrusted storage. Before
it reads a lease or imports the installed candidate runtime, it calls the shared
`read_verified_receipt()` implementation and the typed
`from_verified_installed_receipt()` resolver. Consequently the observation
route accepts only the exact owner-only isolated target declared by the receipt
and only after canonical receipt integrity, base/version/build/source/candidate
tree parity, lexical managed cache topology, recursive installed-payload
manifest identity, owner/mode, and symlink ancestry checks succeed. The
receipt-selected installed plugin root is used byte-for-byte; there is no cache
scan, base-version reconstruction, source fallback, or stable-profile fallback.

This validation happens before receipt identity becomes lease-verifier input.
Every receipt or installed-payload validation failure is represented to the
operator as an unavailable observation stream without a traceback or raw
diagnostic. It remains nonblocking to the actual MCP call, and cannot establish
readiness or a passing live run.

## Nonblocking rule

Observation creation, claim, ready publication, and append can fail safely only
as `limited`. They neither alter a canonical MCP result nor convert a failure
into a success. Conversely, a missing/limited observation can never establish
that the real candidate was ready, that a tool was called, or that an E2E run
passed. The transport prints observations; the LLM/coordinator remains the
only acceptance authority.
