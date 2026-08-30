# Phase D: v19 exact-session lease availability root cause

**Status: corrected at source; candidate and live qualification remain separate gates.**

## Finding

The v19 live run did create and use the intended runtime-owned observation
generation. The event reader failed before it could open that generation
because it revalidated the receipt against the *mutable source checkout* after
the candidate had already been staged and launched. The source manifest was no
longer acceptable at observation time because transient Python bytecode had
appeared beneath the source runtime tree. The receipt verifier therefore
rejected the current source/candidate relation and `cortex-live-smoke events`
collapsed that internal failure into its sanitized “generation unavailable”
result.

This is an observer trust-boundary defect. It is not an MCP-startup failure,
an environment-forwarding failure, or a missing server registration.

## Bounded transition evidence

Only owner-only isolated state was inspected; no raw nonce, identifiers,
worker content, event payload, or private logs were retained.

| Transition | Evidence | Result |
| --- | --- | --- |
| `start` created a session intent | The isolated intent record had the expected v2 session form before cleanup. | Completed. |
| `cortex-dev` consumed it after candidate refresh | The retained lease matched the authoritative candidate receipt on build, stamped version, and candidate location. | Completed. |
| Packaged MCP process claimed the same generation | The lease recorded one process registration and the request record retained the claimed state. | Completed. |
| MCP initialization completed | A matching ready receipt and a bounded non-empty event file existed in the exact generation. | Completed. |
| Event reader opened the generation during live run | The reader instead returned its sanitized unavailable result. A direct current-source receipt verification failed. | Failed. |
| `stop` caused the failure | The cleanup revocation was observed only after the failed event read; the generation, request, ready receipt, and event file remained. | Excluded. |

The isolated candidate's receipt relations and the retained lease relations
were mutually consistent. The failure occurred in the observer's extra
`read_verified_receipt` step, which recomputes the complete *current checkout*
manifest. Its failure was directly attributable to a transient source-runtime
bytecode directory. Thus a real candidate session can be correctly registered
yet become unreadable solely because unrelated local activity changes the
checkout after the candidate refresh.

## Architectural correction

Make the live observation verifier candidate-authoritative after launch:

1. During `cortex-dev` refresh, verify source-to-candidate parity once and
   issue the content-addressed isolated receipt.
2. During `events`, verify the receipt signature, isolated topology, stamped
   candidate path, and candidate payload against the receipt; verify the lease
   and ready/event records against that same immutable candidate identity.
3. Do **not** recompute or require the live checkout manifest on the
   post-launch observation path. A later checkout edit, temporary bytecode, or
   another agent's source validation cannot change what the already running
   candidate is.
4. Continue to prevent source bytecode in the refresh path (for clean
   candidate construction), but treat that as hygiene rather than the live
   observer's trust anchor.

This preserves the strict guarantee: the observer can read only the exact
receipt-selected, content-addressed candidate and its nonce-bound generation.
It removes the unrelated mutable-checkout dependency that made the v19 result
unavailable.

## Implemented phase boundary

`read_verified_receipt()` remains the refresh/launch admission verifier: it
recomputes source-to-installed-candidate parity before Codex starts.
`read_runtime_verified_receipt()` is the sole post-launch verifier used by the
observation reader and lease-request helper. It validates the canonical
owner-only receipt, exact isolated target, explicit `parity_verified=true`
attestation, stamped version/path/base/build claims, and recursive installed
payload digest/topology without reading or hashing the mutable checkout. There
is no source, staged, or stable fallback. A source `__pycache__` created after
staging is therefore not relevant to visibility of the already running
candidate's exact-session event generation; a receipt or installed-tree change
still fails closed.

## Required regressions

- A real isolated candidate receives a valid receipt and exact-session lease;
  mutate only the source checkout afterward (including transient bytecode),
  then prove the observer still reads the matching candidate's ready/event
  generation.
- Change, replace, or mismatch the candidate payload/receipt/lease and prove
  the observer still fails closed before reading events.
- Preserve the current black-box installed-candidate test with the actual
  `cortex-live-smoke events` command, not only direct Python helper calls.
- Rerun the focused LLM-driven live scenario only after this gate proves one
  matching `server_ready` is visible.
