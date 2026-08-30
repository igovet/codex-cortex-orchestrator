# Architecture

- [Candidate observation-generation protocol](candidate-observation-generation-protocol.md) — receipt-anchored pending/claim/ready/limited state machine and transport isolation boundary.
- [Task transport context](task-transport-context.md) — exact connection-scoped task binding, restart behavior, and concurrent-session isolation matrix.
- [Exact-session observation lease](exact-session-observation-lease.md) — nonce-bound intent, candidate lease, atomic claim, restart registration, and revocation state machine.
- [Exact-session observation lease source review](exact-session-observation-lease-review.md) — per-invariant production-boundary review; P1 transition/race evidence remains open.

- [Isolated candidate provenance](candidate-provenance.md): content-addressed
  candidate delivery, parity gates, and black-box qualification.
- The runtime payload closure is defined by
  `plugins/cortex/runtime-payload.json`; the candidate and marketplace gates
  reject missing or unlisted production Python modules.
- Both gates consume the shared recursive implementation in
  `scripts/cortex_payload_manifest.py`, including exact directory-topology and
  root-safety checks.
- Managed cache/version/plugin paths use the same lstat ancestor-chain and
  safe-creation boundary.
- Isolated live-dev marketplace registration is a native-CLI reconciliation
  boundary: only the exact non-symlinked `.cortex-dev/.codex` target may
  replace a stale `cortex` source; same-source reuse, missing registration, and
  unrelated marketplace preservation are regression-tested.
- [Semantic registry](semantic-registry.md): authoritative operation metadata,
  capability edges, and catalogue binding.
- [Domain Kernel](domain-kernel.md): non-autonomous typed transition boundary
  and atomic command-receipt adapter.
- [Phase C acceptance](phase-c-acceptance.md): parity, registry, typed-edge,
  and receipt-metadata gates.
- [Decision aggregate](decision-aggregate.md) — Phase D server-owned decision
  identity, atomic consumption, replay/conflict, reconciliation, and parity.
- [Clarification Hold aggregate](clarification-hold.md) — the typed
  open-before-question and record-before-exact-worker-continuation lifecycle;
  it preserves model-owned orchestration, reconciles only exact
  same-assignment publication evidence when no host callback exists, and
  leaves host delivery explicit.
- [MCP live event journal](mcp-live-event-journal.md) — the bounded,
  owner-only, sanitized observation stream for hidden worker tool outcomes;
  the helper exposes it but the LLM owns acceptance.
- [Phase D hold/event-journal review](phase-d-hold-event-review.md) — an
  independent source review of v18 holds, continuation rendering,
  publication reconciliation, MCP event coverage, and live transport. Source
  clearance is complete; candidate/live remain separate release gates.
- [Decision API schema](decision-api-schema.md) — family-specific clarification,
  plan-review, and steering commands with typed bindings and feature parity.
- [Phase D decision qualification](phase-d-qualification.md) — exact-candidate
  stdio qualification for the decision vertical slice.
- [Phase D candidate qualification result](phase-d-candidate-qualification-result.md) —
  sanitized installed-candidate provenance, plugin-payload manifest-scope and
  canonical-location corrections, and qualification outcome.
- [Decision capability parity matrix](decision-capability-parity-matrix.md) —
  capability-by-capability preservation status and evidence gates for the six
  public decision operations.
- [Phase D adversarial review](phase-d-adversarial-review.md) — source and
  exact-candidate findings/evidence for the Decision cutover; source review is
  recorded there, while the installed-candidate gate remains blocked by the
  qualification fixture/provenance integration failure. The focused LLM-driven
  live-dev gate remains unrun.
- [Phase D candidate root-cause map](phase-d-candidate-root-cause.md) — the
  shared architectural roots, P1 source-remediation evidence, and remaining
  candidate/live acceptance criteria for D-CAND-001 through D-CAND-006,
  including the pre-receipt shard-admission contention root and the remaining
  end-to-end admission scope review.
- [Phase D decision remediation](phase-d-remediation.md) — source-level root
  remediations, ownership, and regression evidence for D-ADV-001 through
  D-ADV-014 and D-CAND-001 through D-CAND-006.
- [Phase D SQLite SIGBUS root cause](phase-d-sqlite-sigbus-root-cause.md) —
  the authoritative D-CAND-006 WAL/SHM ownership decision and exit-code-aware
  exact-candidate qualification evidence; live-dev is the next gate.
- [Phase D installed concurrency root cause](phase-d-installed-concurrency-root-cause.md) —
  v19 derived compact-task locator, canonical row verification, and bounded
  non-authoritative recovery for shared-state multi-project concurrency.
- [v19 task-locator independent review](v19-task-locator-review.md) — source
  clearance for migration, canonical publication, indexed first-call routing,
  recovery safety, lock ordering, and filesystem-policy integration.
- [Phase D focused live verification](phase-d-live-verification.md) —
  operator-controlled live-dev evidence; the historical pre-readiness
  unstamped-path defect is corrected by the authoritative receipt boundary,
  while the latest real attached-client retry remains failed/unverified because
  the visible clarification produced no observed family-specific open/record
  MCP pair and no accepted first worker-owned report event.
- [Phase D live decision root cause](phase-d-live-decision-root-cause.md) —
  the blocking Clarification Hold architecture gap, catalogue/renderer drift,
  missing same-worker continuation bridge, and missing worker-event capture
  boundary exposed by the attached-client live run.
- [Phase D focused live result](phase-d-live-result.md) — sanitized outcome of
  the later real tmux/LLM-driven run: candidate and composer were observed, but
  the route made no first Cortex MCP call, so the run remains failed/unverified.
- [Phase D live first-call root cause](phase-d-live-first-call-root-cause.md) —
  isolated-candidate diagnosis of that earlier boundary: package delivery and
  standalone MCP discovery are sound, but the ordinary session made shell calls
  rather than a Cortex call; a host registration receipt and safe server-ready
  observation are required before another workload is submitted.
- [Phase D live server-ready root cause](phase-d-live-server-ready-root-cause.md) —
  the later startup evidence: the registered plugin has no MCP environment
  propagation configuration, while the observer relies on one; runtime-owned
  candidate-anchored receipt generations replace that unsupported assumption.
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
  diagnosis showing that a fresh test project stopped at Codex directory
  trust before the composer, plus the owner-only output-only observation
  correction and remaining forward acceptance tests.
