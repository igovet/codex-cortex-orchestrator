# Exact-session observation lease

The live observation channel is addressed by a single fixed session identity
and a random per-start nonce. It is not addressed by directory enumeration,
newest timestamps, inherited journal paths, or an environment-selected
generation.

```text
absent
  -> intent (helper: nonce + session + creation epoch, owner-only)
  -> lease (candidate: receipt identity + nonce, signed, one per session)
  -> claimed (MCP: atomic lock claim, one generation)
  -> ready (one signed registration per MCP process)
  -> observed (helper reads only its nonce-bound generation)
  -> revoked/cleaned
```

The helper creates the intent before launching the ordinary shell. After the
isolated candidate has refreshed and its receipt has been verified,
`cortex-dev` consumes the exact intent and replaces any prior pending/active
lease for that same session. The lease contains the candidate path, base
version, build identity, catalogue identity, generation, and creation epoch;
its signature is derived from the unguessable nonce. The nonce never appears
in the pane, launcher command, or user-facing output.

The packaged MCP runtime derives `CODEX_HOME` from its installed candidate,
opens only the fixed session lease, validates ownership, mode, symlink safety,
freshness, receipt identity, and signature, then claims it under a process
lock. A controlled MCP restart reuses the claimed generation and appends one
distinct process registration. Reinitialization by the same process reuses
its registration and cannot create duplicate ready evidence. A revoked lease
cannot be claimed or made ready.

The transport stores the nonce privately in its exact-session state. Event
observation resolves the one lease and its generation using that nonce; it
never scans generation directories or selects the newest record. Stop and
interrupt revoke the nonce-bound intent and lease before removing temporary
transport state. Observation remains best effort and non-blocking: inability
to write or read observations never changes a canonical MCP result and never
establishes readiness or acceptance.

Adversarial qualification must cover stale/tampered intent and lease records,
wrong nonce/signature/build/version/path/catalogue, unsafe modes and symlinks,
crashes between each transition, concurrent claims, controlled restart,
post-revoke claim prevention, crashed-session replacement, multiple old
generation directories, cross-session/build isolation, and event-resolution
drift.
