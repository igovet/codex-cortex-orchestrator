# Exact-session observation lease source review

Review date: 2026-08-29  
Reviewer: independent source reviewer

## Decision

**Source clearance: granted for the complete source protocol.** The production
boundary is fail-closed for the exercised properties: lease records are
strictly shaped, HMAC-signed, nonce-bound, freshness-checked, and bound to
candidate/catalogue identity; claims use one fixed lease and generation under
a lock; and `cortex-live-smoke events` consumes the same verified candidate
receipt and installed-cache resolver as the launcher before opening the exact
generation stream.

The expanded matrix closes the earlier race and absent-tmux gaps, and covers
malformed nonce values through the real MCP claim fallback, truncated
ready/event records, observer-level version/catalogue mismatches, and
replacement before no-follow open. Request and ready are control files that
are only lstat-validated by the observer; replacement is rejected before any
content use. Candidate qualification and live-dev acceptance remain separate
and were not run.

## Invariant matrix

| Invariant | Production evidence | Test evidence | Decision |
| --- | --- | --- | --- |
| Intent/nonce confidentiality | Live helper creates a nonce-bound intent and keeps the nonce in owner-only state. | Private-state/launcher tests and intent fixture. | Pass at source boundary |
| Post-receipt binding | `consume_intent()` and `claim_generation()` bind path, build, version, catalogue, and nonce. | Production-faithful receipt → intent → lease fixture and strict partial-record rejection. | Pass at source boundary |
| One claim/no scan | Fixed `lease.json`, signed generation ID, exclusive lock; no generation enumeration. | Two-process claim race retains one generation and both registrations. | Pass |
| Signed identity/freshness | `verify_lease_record()` enforces exact shape, HMAC, syntax, freshness, session, path, build/version/catalogue; `_read()` normalizes malformed files. | Six identity-drift cases, wrong signed observer identity cases, stale/tampered leases, and malformed nonce/lease/intent through packaged MCP initialize. | Pass |
| Restart/duplicate initialize | Same generation and PID registration are reused; `server_ready` is emitted once after a physical initialize reply. | Restart reuse, concurrent registration, duplicate initialize tests. | Pass for covered paths |
| Revoke/missing tmux | `stop` revokes before exact-session cleanup and never kills the server. | Post-revoke rejection, barrier-synchronized revoke/claim race, and missing-session cleanup. | Pass at source boundary |
| Nonblocking absence | MCP catches claim `ObservationGenerationError` and uses `EventJournal(None, ...)`. | Missing-code-home/journal fallback tests. | Pass |
| Installed-cache topology | Candidate home and receipt/location validators reject unsafe topology and symlinked ancestors. | Receipt/cache/root/nested symlink, mode, payload tests. | Pass for tested topology |
| No env/default route | Journal is created only from a claimed generation. | Legacy environment-route rejection tests. | Pass |
| Exact events lookup/no drift | `events()` verifies receipt, installed location, lease identity/freshness, nonce, generation, owner-only files, then no-follow opens only the selected stream. | Receipt/lease tamper, wrong version/count/digest, symlink/mode, request/ready/event replacement-before-open, candidate-root and journal tests. | Pass at observer boundary |

## Findings

### P1-LEASE-001 — Closed at production boundary; release evidence remains separate

`events()` calls `read_verified_receipt()` and
`from_verified_installed_receipt()` before importing candidate code or
selecting an event path. The validators bind the exact isolated target,
candidate version/build/source/payload digests, managed cache topology,
owner-only modes, and no-symlink ancestry. The lease verifier then binds that
identity to the nonce and advertised catalogue. Recomputed receipt-identity
tamper tests, checksum/mode/symlink tests, and candidate-tree tests prove the
observer does not merely reject malformed JSON.

### P1-LEASE-002 — Closed at source boundary

The dedicated lease matrix covers two-process claim concurrency, same-process
restart reuse, post-revoke rejection, a barrier-synchronized revoke/claim
race, partial intent/lease and truncated ready/event rejection, six direct
identity drift cases, absent-session stop revocation, wrong signed observer
identity, request/ready/event no-follow replacement, and packaged MCP initialize
malformed JSON/nonce record handling. Existing journal/observer suites cover
owner-only mode and symlink substitution. The event file is raced immediately
before its no-follow open; request/ready replacement is rejected by the
owner-only lstat gate before any content use.

The event stream is raced immediately before its no-follow open; request and
ready are control files only lstat-validated by the observer, and replacement
is rejected before any content use. This matches their complete production
behavior. Any future recovery tests must continue
to prove read-only reconciliation or exact signed-lease reuse; they must not
scan for a newest generation or mint a replacement binding.

## Test evidence

With temporary owner-only `HOME` and `CODEX_HOME`,
`PYTHONDONTWRITEBYTECODE=1`, and `python3 -B`, the focused source gate passed:

```text
103 passed, 10 subtests passed in the combined source gate; the dedicated lease
file contains 23 effective collected cases including parametrized entries.
contract-lint: passed
marketplace validation passed
```

The gate covered the lease file, live transport and production-faithful
receipt fixture, observer receipt/lease tamper rejection, concurrent claim and
revoke races, restart reuse, post-revoke rejection, absent-session revocation,
packaged MCP malformed JSON/nonce lease/intent initialization, identity/lexical
drift, observer wrong version/count/digest and event replacement, MCP event journaling, candidate receipt/cache topology, payload closure,
public MCP conformance, semantic registry, and v17 maintenance schema. No
candidate refresh, installed-candidate qualification, or live-dev run was
performed.

## Required closure gates

1. Run exact-candidate qualification without mutating the stable profile.
2. Run separate LLM-driven live-dev qualification; neither gate may be
   inferred from this source review.
