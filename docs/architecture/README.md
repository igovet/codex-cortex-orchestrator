# Cortex architecture artifacts

This directory contains the architecture decisions and parity contracts for
Cortex 1.12.1.

## Documents

- [Candidate observation-generation protocol](candidate-observation-generation-protocol.md) — receipt-anchored pending/claim/ready/limited state machine and transport isolation boundary.
- [Exact-session observation lease source review](exact-session-observation-lease-review.md) — per-invariant production-boundary review; P1 transition/race evidence remains open.

- [ADR-001: Cortex 1.12.1 stabilization architecture](adr-001-cortex-1-12-1-stabilization.md) — target Domain Kernel, semantic registry, receipts, typed capabilities, and candidate provenance decisions.
- [Orchestration feature parity](orchestration-feature-parity.md) — complete capability inventory, ownership mapping, migration status, acceptance tests, and at-risk capabilities.
- [Transactional command receipts](command-receipts.md) — domain-kernel receipt identity, atomic mutation/replay semantics, migration, and qualification matrix.
- [Candidate provenance](candidate-provenance.md) — isolated candidate identity,
  source/candidate parity, native isolated-marketplace reconciliation, and the
  authoritative stamped installed-candidate receipt required before Codex
  launch.
- [Runtime payload manifest](../../plugins/cortex/runtime-payload.json) — the
  canonical launcher-plus-runtime Python closure used by the candidate and
  marketplace gates.
- The closure rules are implemented once in
  `scripts/cortex_payload_manifest.py`, including recursive package and exact
  directory-topology validation.
- Its trusted ancestor-chain checks cover candidate staging, version reuse, and
  installed plugin roots before any filesystem write or digest operation.
- [Semantic registry](semantic-registry.md) — the ordered semantic operation
  metadata and generated catalogue binding.
- [Domain Kernel](domain-kernel.md) — the non-autonomous transition and receipt
  foundation for future vertical slices.
- [Phase C acceptance](phase-c-acceptance.md) — capability identity, registry
  coverage, typed handle edges, and zero-skip drift gates.
- [Phase D decision qualification](phase-d-qualification.md) — exact-candidate
  stdio qualification matrix for server-owned decision bindings, replay,
  conflict, recovery, and concurrency.
- [Phase D candidate qualification result](phase-d-candidate-qualification-result.md) —
  sanitized installed-candidate provenance, plugin-payload manifest-scope and
  canonical-location corrections, and qualification outcome.
- [Clarification Hold aggregate](clarification-hold.md) — the durable bridge
  between a clarification answer and the exact originating worker, including
  private host claim/delivery evidence and parent-linked recovery boundaries.
- [Phase D hold/event-journal review](phase-d-hold-event-review.md) — an
  independent source review of the hold, continuation renderer, publication
  reconciliation, sanitized MCP event journal, and transport boundary;
  source clearance is complete; candidate/live remain separate release gates.
- [Decision capability parity matrix](decision-capability-parity-matrix.md) —
  decision-family capability ownership, implementation locations, and the
  source/candidate/live evidence required to preserve every decision behavior.
- [Phase D adversarial review](phase-d-adversarial-review.md) — source and
  exact-candidate review of the six-operation cutover, aggregate transaction,
  public handle projection, and qualification evidence; the exact-candidate
  gate is closed, while the focused LLM-driven live-dev gate remains open.
- [Phase D SQLite SIGBUS root cause](phase-d-sqlite-sigbus-root-cause.md) —
  canonical D-CAND-006 sidecar-lifecycle decision, mmap-safety boundary, and
  source/exact-candidate qualification evidence; live-dev remains the next
  gate.
- [Phase D installed concurrency root cause](phase-d-installed-concurrency-root-cause.md) —
  v19 server-owned derived compact-task locator, canonical verification, and
  bounded recovery correction for multi-project first-call contention.
- [v19 task-locator independent review](v19-task-locator-review.md) — source
  clearance and isolated 80-shard/160-process regression evidence for the v19
  correction and filesystem-policy integration.
- [Phase D focused live verification](phase-d-live-verification.md) — the
  operator-controlled tmux/ordinary-Codex gate; candidate receipt/provenance
  passed in the latest attached-client retry, but live remains
  failed/unverified because no family-specific clarification open/record pair
  or accepted first worker report event was observed.
- [Phase D live decision root cause](phase-d-live-decision-root-cause.md) —
  root-cause analysis of that live failure. The required repair is a
  server/host Clarification Hold boundary with exact-worker delivery and
  sanitized worker-event capture; it preserves model-owned orchestration.
- [Phase D focused live result](phase-d-live-result.md) — the subsequent real
  isolated run. It passed the candidate/composer transport prerequisites but
  made no first Cortex MCP call, so it is failed/unverified and not live proof.
- [Instruction-surface contract cleanup](contract-cleanup.md) — package-wide
  semantic-operation parity, MCP-schema ownership, clarification continuation
  invariant, and preserved orchestration capability matrix.
- [Orchestrator activation kernel](orchestrator-activation-kernel.md) — the
  pre-anchor route state machine, passive host receipt boundary, and complete
  post-anchor feature-preservation matrix.
- [Official Codex activation kernel](official-codex-activation-kernel.md) —
  current OpenAI skill/hook constraints, plugin hook state machine, and source
  acceptance evidence.
- [Phase D Codex TUI root cause](phase-d-codex-tui-root-cause.md) — sanitized
  diagnosis of the fresh-project trust gate and detached TUI evidence loss,
  plus the owner-only output-only transport correction and remaining live gate.

The parity matrix is the preservation contract for the redesign. The ADR is
the architectural decision record; the candidate-provenance document defines
the delivery qualification boundary. Runtime changes must satisfy all three
artifacts and must not remove existing orchestration capabilities. Phase C is
a required pre-live gate.
