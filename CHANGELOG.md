# Changelog

## [1.15.6] - 2026-09-03

### Changed

- Worker guidance now resolves ordinary execution blockers autonomously: no
  execution-time user questions, approval loops, or private lineage fields may
  be emitted in a publication. Plan review remains optional for complete
  low-risk work and is required only for high-risk/material plans or an
  explicit user request.
- Added regression coverage for all 22 packaged profiles, private-publication
  schema boundaries, and multiple in-flight live-message turns.
- `close_task` now requires the current user-facing closure review and an
  explicit `close` choice; `revise`, missing, and stale reviews remain open.
- Added a deterministic full-facade matrix covering every public operation and
  all 22 packaged profiles with model/effort assertions.

### Fixed

- Removed stale per-profile escalation text that routed routine blockers back
  to the user and could cause unsupported publication metadata such as
  `parent_assignment_ref`.
- Clarified worker publication contracts so assignment lineage and predecessor
  references remain server-owned and cannot be supplied by a worker.

## [1.15.5] - 2026-09-03

### Changed

- Made Luna the default route for bounded discovery, implementation, QA, and
  deterministic rechecks; Terra is reserved for genuinely complex planning or
  architecture, while Sol is limited to rare very-high-risk security work.
  Every route supports effort through `max`; `ultra` is never accepted.
- Added the live qualification fixture for all 22 packaged agent profiles and
  all 20 public operations; the real CLI/Desktop matrix remains an operator-run
  gate and is not claimed as completed by this source release.

### Fixed

- Approved plans now authorize bounded in-contract rework automatically. A
  partial, blocked, stale, unverified, or contradictory delivery result is
  immediately exposed as `rework_assignable` and linked to its predecessor;
  no repeated steering question or plan approval is required.
- Stale worker publications now report the authoritative assignment lifecycle
  state before resolving current outcome names, preventing misleading
  `outcome_item_not_found` errors after a contract revision.
- Synchronized release metadata and documentation with the current model
  policy and cache-stamped package payload.

## [1.15.3] - 2026-09-03

### Changed

- A concrete user-authored requirement change is now recorded directly as
  steering; the coordinator opens a steering question only when a real branch
  still requires the user's choice.
- Plan review is derived from authoritative governance depth and plan evidence:
  complete risk-free minimal plans proceed informationally, while light/full or
  uncertain plans require review. A planner can no longer self-attest a review
  downgrade.

### Fixed

- Bound native worker leases to the child transcript thread before agent/turn
  fallbacks so parallel Desktop workers cannot overwrite one another when a
  publication hook omits `agent_id`.
- Made a terminal worker stop without publication route immediately through
  lineage-linked loss recovery instead of replaying the original assignment or
  waiting for the user to say “continue”.

## [1.15.2] - 2026-09-03

### Changed

- Kept Codebase Memory as the preferred worker discovery route while allowing
  exactly one safe, assignment-scoped native fallback when the graph is absent,
  denied, unusable, or demonstrably insufficient.
- Clarified autonomous in-contract continuation after worker evidence without
  adding a host scheduler or weakening genuine plan, steering, clarification,
  and closure decisions.

### Fixed

- Replaced language and keyword plan heuristics with an affirmative structured
  nonmateriality proof. Informational review now requires every materiality
  predicate to be proven absent; missing, unknown, malformed, or material facts
  require review regardless of language or governance depth.
- Atomically revoked nonterminal pre-revision worker authority when semantic
  steering advances the effective contract. Stale assignment reads,
  publications, continuation projection, ownership, approval binding, and
  downstream dispatch now fail closed while immutable history is preserved.

## [1.15.0] - 2026-09-02

### Changed

- Replaced the overloaded coordinator `read_task` views with purpose-specific
  `read_state`, `read_scope`, `read_outcome`, `read_continuations`,
  `read_evidence`, and newest-first `read_timeline` operations. Coordinator
  reads now return only the data needed for the next action; there is no public
  full-contract read.
- Restricted `read_task` to worker assignment consumption. A worker receives
  only its immutable assigned outcomes and the predecessor evidence selected
  for that assignment; `view` and caller-selected `report_policy` are no longer
  valid worker inputs.
- Changed steering retirement to accept exact current outcome names from
  `read_scope`. Point replacements use `read_outcome` only for the selected
  outcome, and add-only or no-change steering no longer requires a redundant
  post-review state read.
- Expanded the public catalogue from fourteen to twenty operations while
  keeping the complete serialized `tools/list` response below 65,536 bytes
  with a tested safety reserve.

### Fixed

- Made a confirmed lost-worker replacement derive its complete outcome scope
  server-side when exactly one predecessor is recoverable, avoiding fragile
  model-side repetition of long semantic outcome names while preserving exact
  selection for ambiguous multi-predecessor recovery.
- Made `open_plan_review` return the verified active-plan Markdown link in its
  own success receipt so the immediate user decision packet never reconstructs
  or shortens a link from an earlier evidence read.
- Made documentation publication preserve and derive verification facts from
  the already-required outcome coverage when a worker does not duplicate the
  same evidence in the optional `verification_facts` array, preventing a
  first-call validation failure without weakening plan or result evidence.
- Required post-clarification behavior changes, including changes stated after
  plan review or recovery, to pass through durable steering before assignment
  instructions may use them.
- Replaced very long user-facing shard/report paths with verified short
  content-addressed plan/report links under the host-private Cortex view root,
  preventing CLI or Desktop models from dropping identifier fragments.
- Made the live evidence and plan-review contracts reject a bare path as a
  substitute for the complete server-provided Markdown link.
- Made successful task closure repeat every verified finalized plan/report link
  so the immediate final answer never reconstructs an earlier path from memory.
- Stopped coordinator state reads from being used as worker-liveness polling
  between unchanged native waits.
- Routed coordinator recovery from one scalar `read_state` directly to
  `read_continuations` when delegated work remains; `read_timeline` is reserved
  for an explicit chronology or audit need instead of being used as a recovery
  lookup.
- Added canonical audience-specific routing state machines: coordinator
  transitions live in the orchestrator skill, while worker transitions live in
  `cortex-control` so the automatic compaction hook reloads the correct table
  for each audience.
- Routed a question known to choose previously unstated product behavior
  directly through steering, so the direct answer updates the contract without
  a redundant clarification/confirmation pair. Ordinary clarification remains
  factual-only; an unexpected semantic change still opens steering before any
  assignment can use it.
- Made plan and finalized-report evidence return verified host-private Markdown
  links for user-facing review, without renderer-added Markdown backslashes or
  character-entity substitutions.
- Added a persistent-stdio conformance flow that successfully invokes every
  public operation, including planner and delivery worker publications,
  point steering, evidence inspection, and closure.

## [1.14.17] - 2026-09-02

### Fixed

- Made durable clarification, steering, and plan-review openings explicitly
  separate from their user-visible presentation. After a hold opens, the
  coordinator now renders the complete localized decision context, safe
  choices, and consequences instead of assuming tool arguments are visible in
  CLI or Desktop.
- Required plan review to include a decision-ready summary and the verified
  current-plan link when available; a bare “plan ready” approval request is no
  longer valid coordinator communication.
- Required every native worker escalation to publish the blocked action,
  established evidence, exact missing decision, safe choices, and consequence
  of each so the coordinator can ask a detailed question without forwarding
  raw or context-free worker prose.
- Made terminal coordinator evidence return verified clickable links for the
  selected active plan and finalized report set, with those links leading the
  model-visible response in both CLI and Desktop.
- Preserved authored Markdown in generated plan and report files without
  renderer-added backslash escapes or character entities.
- Clarified the ordinary-question input contract so the optional closure-only
  choices field is omitted rather than sent as an invalid empty array.

## [1.14.16] - 2026-09-02

### Fixed

- Enforced the documented coordinator bootstrap order at the host boundary.
  After an explicit Cortex route selection, the activation guard now permits
  `open_task` as the only pre-anchor coordinator Cortex operation; an attempted
  placeholder governance/task call is denied before it reaches MCP schema
  validation or the ledger. Native worker assignment bootstrap is unchanged.
- Made the MCP session instructions and governance tool contract state the
  fresh-connection precondition adjacent to model tool selection: coordinators
  call `open_task` first and must never invent a placeholder `task_ref`.

## [1.14.15] - 2026-09-02

### Fixed

- Restricted the bundled Cortex catalogue to direct model-visible calls by
  excluding it from both programmatic code mode and deferred discovery. Native
  Desktop workers can no longer receive `read_task` only inside
  `functions.exec`/`ALL_TOOLS` while another worker in the same host receives
  the proper direct MCP tool.
- Made the first late-adopted Desktop worker assignment read tolerate and
  ignore a copied `report_policy`, including `latest_for_scope`. The immutable
  assignment evidence policy remains server-owned, while the harmless field no
  longer causes a bootstrap `validation_error` before the exact receipt is
  consumed.
- Applied the existing same-connection active-task fallback before input-schema
  validation. Task-anchored coordinator calls may therefore omit a repeated
  `task_ref` after `open_task` without a false missing-field error; the public
  schema remains complete and required, and connections without an active task
  still fail closed.

## [1.14.14] - 2026-09-02

### Fixed

- Made the bundled Cortex MCP required at host session startup and excluded
  its complete fourteen-operation catalogue from deferred discovery. A new
  Desktop task therefore cannot receive the selected orchestrator skill while
  silently omitting `open_task`; the host must expose the intact direct
  catalogue before the first turn or fail session initialization explicitly.

## [1.14.13] - 2026-09-02

### Fixed

- Made coordinator state reads expose one authoritative top-level pagination
  marker. Timeline pages now continue through the same server-owned
  `read_task` connection in bounded 16-event slices; nested `data.has_more`
  and `data.next_sequence` markers are no longer exposed, and fresh-state
  admission is recorded only after the terminal page. This prevents the model
  from following a nested `has_more=true` into a server-declared terminal read
  and preserves the complete ordered state instead of truncating it.
- Applied the dedicated 224 KiB report-response envelope when assembling
  multi-report worker handoffs. A valid planner assignment with several
  finalized predecessor reports no longer fails at the unrelated 65,536-byte
  single-storage-value limit; complete evidence is returned or paginated
  through the existing server-owned continuation without truncation.

## [1.14.12] - 2026-09-02

### Fixed

- Kept every Cortex operation as a separate direct model-visible call for both
  coordinators and workers. The activation guard now rejects only Cortex calls
  hidden inside programmatic `exec`, while ordinary non-Cortex composition is
  unchanged. The wire catalogue omits optional `outputSchema` declarations but
  delivers the complete description and closed `inputSchema` for all fourteen
  operations in one exact response; complete result schemas remain enforced
  privately by the server.
- Made coordinator recovery discard pre-compaction state before constructing
  exact decision mutations, so steering outcome replacements are copied from a
  fresh current-state read instead of reconstructed from a summary. Directly
  surfaced calls are ordered by the host guard; steering additionally requires
  one same-connection state read after the decision opens, so an outer
  programmatic-tool call cannot record before recovery evidence reaches the
  MCP server.
- Allowed a consumed worker assignment to be reconciled read-only on its same
  authenticated MCP connection after context compaction or reset. Fresh or
  copied connections remain rejected, page receipts are not duplicated, and
  publication remains blocked until every reconciled page is consumed.
- Required workers to rebuild terminal publication coverage from the fresh
  server-owned assignment reconciliation projection after compaction. The host
  guard keeps publication blocked until the original worker connection has
  completed that recovery read through its terminal page.
- Kept one host lifecycle authorization valid across every successful page of
  a paginated worker assignment. Intermediate `has_more=true` pages no longer
  revoke the already claimed persistent-connection bootstrap, while worker
  publication remains denied until the terminal page.
- Made explicit lost-owner recovery compatible with broad finalized-report
  input policies. Unrelated report authors can no longer conflict with the
  exact predecessor derived from the advertised recovery outcome scope.
- Prohibited timeout-driven interruption of an active worker and duplicate
  steering requests for unfinished work or gates already covered by the
  current approved contract.

## [1.14.9] - 2026-09-01

### Fixed

- Added signed, one-shot `worker_candidate` attestations bound to the exact
  child thread/session/assignment. Initialize selects only the restricted
  candidate catalogue; the exact first worker `PreToolUse(read_task)` signs a
  child/turn/session/assignment/tool-use authorization which the server alone
  can consume for that connection. No guessed or forwarded thread environment
  identity participates in authorization.
- Projected `tools/list` by immutable audience while retaining independent
  authoritative server checks; worker candidates/workers see only `read_task`
  and the three publication tools.
- Narrowed the actual worker-candidate `read_task` advertisement to the exact
  worker reference, the sole assignment view, and bounded continuation. This
  remains compatible with hosts that reuse the common read declaration while
  excluding coordinator-only view values. Unknown fields remain rejected without claim,
  role, cursor, assignment, or ledger mutation.
- Replaced ambiguous worker bootstrap/publication `capability_stale` outcomes
  with `assignment_not_consumed`, `wrong_connection`, `connection_lost`,
  `assignment_stale`, or `publication_conflict`, each with a bounded recovery
  action and no role/report mutation on rejection.
- Added approval-free, shell-free exact skill reinjection through the supported
  `SessionStart(source=compact)` context channel with
  `additionalContextLimit=0`; `PostCompact` remains observation-only and
  repeat skill loading remains explicitly allowed.
- Kept semantic version `1.14.9`; release identity changes only through the
  content-addressed cache suffix.
- Required every worker to establish Git-worktree capability with a bounded,
  failure-normalizing probe before invoking Git. Non-Git canonical project
  roots are now successful observed evidence and Git-dependent inspection is
  skipped instead of producing a speculative nonzero command failure.
- Made the fresh-worker bootstrap name the assignment-reading operation and
  distinguished it explicitly from coordinator-only assignment creation in
  both the server-rendered brief and advertised tool descriptions.
- Made the preload boundary unambiguous: after explicit activation, the first
  task-specific output or action must be the single `open_task` call. The
  coordinator may not emit an activation acknowledgement, commentary,
  question, plan, or result before that call succeeds.

## [1.14.8] - 2026-09-01

### Fixed

- Made worker discovery of planned, optional, or not-yet-created paths
  existence-aware. Expected absence is now recorded as successful evidence
  instead of invoking a command directly against a missing path and producing
  a nonzero failure during planning or verification.

## [1.14.7] - 2026-09-01

### Fixed

- Refined skill bootstrap recovery so compaction/reset can reload the complete
  orchestrator and control contracts through the host loader or sandboxed
  read-only access to the exact installed paths. Both initial load and recovery
  reload remain approval-free; elevated reads, MCP-resource substitutes, and
  project copies fail closed.

## [1.14.6] - 2026-09-01

### Fixed

- Restored host-owned plugin-skill activation: the activated orchestrator
  context explicitly forbids shell/filesystem or MCP-resource self-reads of
  its installed `SKILL.md`, and live workloads must use the real
  `$cortex:orchestrator` token or host skill picker rather than decorative
  bracket text. This removes the regression that asked users to approve `cat`
  against a content-addressed plugin cache path.
- Strengthened the advertised first-call `project_root` contract to require
  the existing canonical host/workspace directory and to prohibit deriving a
  not-yet-created output/package/artifact child as the task root.

## [1.14.5] - 2026-09-01

### Fixed

- Made every bounded successful MCP response, including medium-sized one-shot
  worker assignment pages, self-contained as deterministic JSON `TextContent`
  as well as `structuredContent`. This prevents hosts that retain structured
  data only in lifecycle events from hiding already-consumed authority and
  provoking a non-retryable duplicate bootstrap read.
- Replaced the former quarter-operation text cutoff with a complete encoded
  tool-result bound that reserves space for the JSON-RPC envelope; genuinely
  oversized results still use the fixed structured-content notice without
  risking an overlong stdio frame.

## [1.14.4] - 2026-09-01

### Fixed

- Bound every public aggregate-coverage disposition to its exact semantic
  outcome and exposed explicit ownership plus delivery-assignability state.
  Coordinators can now select only newly assignable post-steering outcomes on
  the first call instead of inferring identity from positional coverage rows.
- Preserved fail-closed admission for mixed owned/new, terminal-owned, stale,
  retired, duplicated, and unconfirmed-loss scopes; rejected requests still
  perform no mutation, while confirmed nonterminal loss remains restricted to
  the atomic lineage-linked recovery path.
- Added coordinator and advertised-contract guidance plus regressions proving
  new-only post-steering delivery succeeds once and mixed ownership conflicts
  remain non-mutating.

## [1.14.3] - 2026-09-01

### Fixed

- Removed consumed-worker publication recovery on fresh MCP connections. Worker
  bootstrap and publication now require the exact digest-attested native child
  and original monotonic worker connection; coordinator connections, direct
  clients, and copied locators fail closed without report mutation.
- Added ordered assignment-authority and predecessor-evidence pagination with
  server-private continuation, digest-only page receipts, exact non-mutating
  current-page restart, terminal consumption gating, and lossless UTF-8
  fragmentation at byte boundaries.
- Replaced implicit nonterminal worker replacement with explicit lost-assignment
  lineage. A confirmed delivery loss requires blocked/aborted state, a concrete
  reason, and non-empty evidence; Cortex atomically stales the old lease, stores
  immutable evidence, and links one successor. Timeout, expiry, reconnect,
  report references, and bare assignment references grant no recovery authority.
- Sanitized lifecycle receipts so they retain only bounded routing categories
  and digests, never task/worker locators, native message plaintext, or
  assignment bodies.
- Correlated a real `SubagentStart` audience with the worker MCP process by
  resolving the same owner-only plugin data directory from hook `PLUGIN_DATA`
  or MCP `CODEX_HOME`. The first successful assignment read atomically commits
  the worker role; direct clients and coordinator processes cannot claim it.
- Ordered every short native-routing discriminator and publication header
  before potentially long message, change, and evidence fields so Codex result
  compaction cannot hide required first-call inputs.
- Attributed worker assignment reads and publications as `role=worker` and
  `scope=assignment` in the sanitized structured event journal, including the
  assignment digest, without exposing the worker locator.

## [1.14.2] - 2026-09-01

### Fixed

- Restored worker publication after a legitimate MCP connection loss by
  rehydrating only the exact durable assignment capability that the worker had
  already consumed. Recovery remains read-only and requires matching project,
  task, assignment, immutable revision, package/catalogue provenance, and
  dispatch correlation.
- Preserved exact same-connection publication and every fail-closed boundary:
  minted, foreign, malformed, stale, partial/different-bound, or drifted
  relations cannot publish, mutate capability state, select by recency, or
  replace another connection binding.
- Added store, domain, and real two-process source-stdio regressions, including
  steering after consumption to prove publication remains bound to the
  immutable assignment revision.

## [1.12.2] - 2026-08-31

### Fixed

- Preserved closed, operation-specific callable handle schemas in compact MCP
  outputs so evidence consumption and report publication no longer fail after
  a successful ledger commit because unrelated canonical handles leaked into
  post-commit validation.
- Required failed or partial QA evidence to create owned corrective work and an
  independent rerun of failed and affected checks before closure.
- Advertised packaged assignment classes and their governance prerequisites so
  rework profile selection is admissible on the first attempt; a corrected
  retry no longer hides a routing failure from live acceptance.
- Isolated native-dispatch receipts by coordinator session and added an atomic
  active-dispatch index under the session lock. Settled or foreign history,
  filename order, and timestamps no longer participate in worker routing.
- Installed PyYAML explicitly in the GitHub release gate and synchronized
  first-call, catalogue-digest, release, and rework regression coverage.

## [1.12.1] - 2026-08-28

### Fixed

- Removed active single-call report writes: all new reports use the assembled
  `begin` → `append` → `finalize` or `abort` protocol, while historical
  finalized one-chunk evidence remains readable.
- Required caller-generated idempotency keys for every mutation, including
  exact replay/conflict behavior after a post-commit response loss.
- Made current effective-contract coverage deterministic across steering,
  ownership transfer, supersession, report arrival order, and closure review.
- Replaced static native-host argument projection with a host-neutral dispatch
  brief, and restricted report bodies to declared consuming delegations.
- Aligned UTF-8 operation budgets, maintenance/index recovery, prompt-only
  decisions, advisory closure evidence, source packaging, and documentation
  with the V12 stabilization contract.

### Verification

- Added the deterministic source-mode all-15-operation first-valid-call regression
  and the owned-PTY `scripts/cortex-live-smoke` verifier.

## Historical/internal milestone — 2026-08-28 (pre-1.12.1 development)

- Added fixed canonical semantic report schemas with a single unchanged
  `source_text` value and non-gating semantic classification.
- Restricted ready plan approval relations to completed semantic-valid
  canonical plan reports while preserving legacy and invalid reports as
  immutable evidence.
- Restored public bounded `single` submission alongside the assembled
  `begin` → sequential `append` → `finalize` (or `abort`) report protocol.
- Required caller-provided idempotency keys on every ledger mutation, with
  byte-equivalent same-key replay and non-mutating different-payload conflicts.
- Restricted report bodies/chunks to declared consuming workers through
  `consumer_delegation_ref`; coordinator reads return metadata/manifests only,
  while worker body reads produce structural consumption receipts.
- Replaced language-suffixed decision fields with neutral `prompt` and exact
  original-language `response_original`; retired `prompt_en` and `response_en`
  are not accepted for new decisions.
- Documented semantic delegation receipts, scoped worker context, aggregate
  request/rendering budgets, host lifecycle/schema boundaries, and advisory
  governance/closure behavior. Documentation impact was material and the
  affected project/feature/security/help surfaces were synchronized.

## [12.0.0] - 2026-08-27

### Added

- Add the packaged coordinator-communication policy. Coordinator-to-user
  messages lead with result, impact, and next step; use the latest meaningful
  user language; suppress unchanged waits; hide internal ledger details by
  default; progressively disclose technical detail; and permit only safe
  optional contextual humor. Worker and durable ledger/report content remain
  English.
- Include the eleventh packaged skill in source candidates and require its
  deterministic lint, marketplace, and release-gate checks. This is static
  contract enforcement only; no runtime dispatcher, A/B evaluation, or
  model-quality gate was added.

### Changed

- Replace the V11 control plane with an opt-in, durable, nonblocking V12
  coordination ledger. The coordinator model owns orchestration, governance,
  rework, verification depth, documentation impact, closure, and final
  synthesis; the backend enforces only schema/size, references, project
  isolation, idempotency, and SQLite integrity. V12 ships no lifecycle hooks,
  workflow gates, waves, receipts, host binding, or server recovery state
  machine.
- Make the root coordinator orchestration-only. All source/project discovery,
  substantive domain analysis, implementation, commands, testing, and direct
  verification belong to workers. The sole read exception is the bounded
  orchestrator-owned route through applicable repository instructions and
  indexed task-relevant knowledge pages, used to compile a six-part semantic
  contract per delegation. That exception now requires non-shell direct reads
  of already-known exact paths and grants no shell/search/graph discovery;
  project-root discovery and project-local state/artifact checks, including
  absence/unchanged verification and `.codex`, remain worker-owned.
- Expose one uniform closed catalog of exactly eleven public tools:
  `create_task`, `inspect_task`, `create_delegation`, `read_delegation`,
  `submit_report`, `read_reports`, `set_governance_mode`, `record_initiative`,
  `inspect_governance`, `submit_governance_closure`, and
  `record_user_decision`.
- Make `create_task` the only explicit `project_root` boundary. Its versioned
  task contract keeps an English objective and bounded result requirements
  beside the exact original request and user language. Every later public call
  uses the returned opaque `task_id`; direct worker briefs carry the canonical
  root for native working context.
- Require delegation `scope` to be a non-empty textual ownership boundary and
  require the coordinator to choose an exact model/effort for every worker.
  Luna remains the default and omits a native model override; Terra and Sol
  pass exact overrides; all preserve `low`, `medium`, `high`, `xhigh`, or
  `max` effort with `fork_turns="none"`.
- Add immutable one-chunk and chunked report submission. A stable report ID can
  move through `begin`, ordered labeled `append`, exact-manifest `finalize`, or
  `abort`; bounded `read_reports` returns only whole selected JSON chunks and
  metadata within cursor and byte limits. Task/delegation inspections expose
  compact report references only.
- Add `plan` reports with informational or coordinator-owned required review,
  plus append-only `record_user_decision` evidence bound to exact subject IDs
  and plan/report digests. Original user responses are retained beside English
  normalization. Decisions are coordinator-attributed evidence, not
  authentication, cryptographic attestation, bearer approval, or backend
  permission.
- Store model-owned `minimal`, `light`, and `full` assessments, append-only
  initiative revisions and project-scoped links, dependency warnings, and
  advisory `ready`, `ready_with_risks`, or `not_ready` closures. User mode
  overrides remain effective across later model assessments; no advisory state
  can prohibit a safe next step or final answer.
- Add owner-only, host-private Markdown task views beside the canonical V12
  database. Task, plan, delegation, report, decision, and paged-timeline views
  are disposable projections with atomic best-effort materialization, tamper
  conflict preservation, source-sequence/digest verification, and dynamic
  `ready`, `stale`, `conflict`, `unavailable`, or `disabled` status. Cortex
  writes no generated file or directory under `project_root`.
- Add a task-ID-anchored host-private operator CLI outside MCP for health,
  sealed whole-project-shard backup, checkpoint, optimize, vacuum, strictly
  offline restore, derived-projection prune/regeneration, and explicit backup
  retention. Mutations require exact confirmation strings; prune/retention
  default to dry-run; restore requires verified MCP quiescence plus exact
  `RESTORE`, task/shard, backup, and `MCP_STOPPED` acknowledgement.
- Keep inter-agent messages and durable operational content in English while
  preserving user-authored original fields and localizing coordinator-to-user
  communication. Verified ready absolute view links are always paired with a
  localized summary; unavailable views fall back to an inline canonical-ledger
  summary without blocking work.
- Make the final documentation-impact decision conditional but mandatory:
  material verified changes receive a documentation-sync worker and separate
  verifier, while no-impact work records a report-grounded
  `documentation not required` rationale without meaningless edits.

### Compatibility

- Start a new host-private V12 database family at
  `~/.codex/cortex/v12/projects/p-<hash>/cortex.db` with additive schema-v1
  migration history and owner-only permissions. The single pre-release V12
  expansion preserves tasks and converts each legacy report body to one
  finalized canonical chunk. V12 never opens, migrates,
  deletes, or modifies V11 databases or legacy project-local state. V11 tools
  and unfinished V11 tasks are intentionally incompatible with V12.

## [11.0.1] - 2026-08-24

### Fixed

- Harden the v11 worker boundary with a fixed-size opaque repair handle backed
  by immutable, task-cascaded schema-v19 escrow. Exact signed released
  schema-v17/schema-v18 histories upgrade transactionally in place to v19 and
  retain their append-only migration rows. The exact signed legacy V1--V8
  namespace is archived privately before a fresh schema-v19 ledger is created;
  its task authority is not migrated or selectable, while every unknown history
  fails closed and is not automatically quarantined.
- Shorten the signed repair handle, retry malformed model copies against the
  same immutable escrow, and expose self-contained per-path patch diagnostics;
  correctly shaped handles that fail integrity remain terminal.
- Replace worker capability pairs and bootstrap repair messages with one exact
  native dispatch authority preserved on every worker-side Cortex call; recovery is
  server-returned, same-child, and never reconstructs worker authority.
- Keep immutable briefings compact and task-specific, with native dispatch
  authority as the sole worker transport authority and no duplicate task intent.
- Add atomic `finalize_worker_failure` cleanup for exact nonretryable child
  terminals without deleting forensic receipts/events/repair escrow or
  creating a result, continuation, replacement, or resumable orphan.
- Minimize public response projections and align package validators, prompts,
  profiles, and release metadata with the v11 contract.

### Changed

- Adopt the approved breaking no-legacy public-input contract without changing
  the package version: the manifest remains **11.0.1**. The coordinator model
  supplies the initial worker waves; the backend validates,
  persists, and dispatches it rather than selecting a replacement pipeline.
- Split every public semantic action into its own MCP tool. Each tool owns its
  complete closed one-level schema in `public_contracts.py`, and runtime
  validation consumes that same schema. Tool descriptions stay short and
  semantic; skills and prompts contain no copied argument fields or schemas.
  The hard cut retains no multiplexed selectors or compatibility aliases.
- Split worker submission and same-attempt repair into `submit_attempt` and
  `repair_attempt`, preserving digest/capsule binding and immutable escrow.
- Make every growing read use its exact server-issued opaque `c11p` cursor.
  Fixed receipts and atomic repair cards do not paginate.

## [11.0.0] - 2026-08-24

### Changed

- Make explicit server-issued task and audience-specific capability authority
  the only public coordinator and worker authorization contract.
- Preserve native V2 `spawn_agent` and exact-child wait/follow-up as the only
  worker lifecycle transport; hooks remain bounded telemetry only.
- Replace compatibility lifecycle and completion forms with the then-current
  v11 public facade, compact typed responses, and canonical AttemptResult and
  AttemptEvent persistence.
- Add digest-bound structural repair for invalid planner and outcome
  submissions, with aggregated JSON Pointer diagnostics and validation before
  mutation.

## [10.0.7] - 2026-08-22

### Fixed

- Make prompt volume targets advisory only. Canonical task, plan, result,
  event, question, answer, governance, manifest, artifact, and handoff data is
  stored and read losslessly; backend admission no longer rejects or silently
  projects valid content because of byte, character, or file-size targets.
- Make large immutable briefings cursor-readable without a hidden transport
  quota while preserving strict identity, digest, path, and lifecycle checks.
- Preserve complete predecessor context and planning/scoping projections across
  successor dispatch and compaction-safe recovery.

## [10.0.6] - 2026-08-22

### Fixed

- Keep canonical blocked and failed worker results addressable as explicit
  non-success terminal receipts without allowing fake success continuations.
- Make the canonical `attempt_result_ref` unambiguous in worker completion and
  governance-close instructions; projection references remain non-authoritative.

## [10.0.5] - 2026-08-22

### Fixed

- Carry the planner work-breakdown sibling through the then-current completion
  transport and persist its immutable planning projection
  atomically, while requiring the final native child close in live audits.

## [10.0.4] - 2026-08-22

### Fixed

- Bind parallel-wave continuation to the complete ordered set of canonical
  results, rejecting partial or cross-slot result submissions before mutation.

## [10.0.3] - 2026-08-22

### Fixed

- Require every native worker to be closed only after its canonical result was
  read and successfully consumed by the server continuation, before any
  successor dispatch.

## [10.0.2] - 2026-08-22

This patch closes terminal replay and live-evaluation gaps found by the
10.0.1 end-only C3 source-mode gate.

- Reject a facade attempt projected `passed` without its exact finalized
  canonical result in finalization, terminal invariants, and close audit.
- Correlate neutral native terminal waits only after an ordered successful
  result-read, server continuation, and child-close sequence.
- Permit an evaluator answer only through a narrow, explicit scenario fixture
  authority; require the durable question presentation, answer, resume, and
  same-attempt terminal sequence before acceptance.

## [10.0.1] - 2026-08-22

This patch makes orchestration completion fail closed whenever a dispatched
facade attempt lacks its finalized canonical `AttemptResult`.

- Block gate passes, generic handoffs, terminal acceptance, and coordinator
  stop completion while an active non-invalidated attempt has no finalized
  result; a native child binding remains recovery metadata, not completion
  evidence.
- Require the coordinator protocol to read the canonical result, obtain a
  successful server-derived continuation, and only then close the exact child.
- Make live source-mode acceptance audit the complete native sequence and one
  matching terminal result per accepted attempt; ambiguous native identity in
  parallel source-mode observation fails closed.

## [10.0.0] - 2026-08-22

This breaking release makes the database-centric attempt protocol the only
worker transport. The public v10 facade used the then-current operation set;
workers checkpointed `AttemptEvent` facts and closed a semantic
`AttemptResult` through its completion transport. Cortex owns identity,
timestamps, changed paths, verification observations, read receipts, and
result projections. The prior 9.x draft/report transport is historical and is
not part of the v10 public contract.

- Added the fresh-only public facade and strict coordinator/worker projections.
- Promoted SQLite schema v15 and the Prompt Contract v3 compiler.
- Separated `WORK_COMPLETED` from finalization and projection retries so
  infrastructure failures never respawn a completed worker.
- Replaced text acknowledgement markers with server-owned briefing and
  predecessor-result receipts.

## [9.3.0] - 2026-08-22

This additive release introduces the database-centric worker attempt protocol.
Workers publish semantic attempt events and one compact completion result while
Cortex owns durable attempt state, server-observed receipts, and generated
human/compatibility projections. Legacy draft/report operations remain
available only as compatibility adapters for older workers.

## [9.2.24] - 2026-08-21

This source-tree patch keeps a coordinator turn alive while its durably bound
native worker is still running, and makes missing worker bootstrap inputs a
durable question/resume boundary.

- Register the Codex `Stop` hook and block coordinator finalization only when
  the active ledger contains a durably bound running worker. The prompt is
  identifier-free, directs the same turn to wait only for the exact persisted
  child, and honors the host's re-entrant stop-hook escape.
- Keep `SubagentStop` as terminal-state telemetry rather than a synthetic
  parent wakeup. An already-idle coordinator is resumed safely through one
  state inspection and its existing child/report/recovery receipt; no child is
  replayed or respawned.
- Require the immutable worker bootstrap to validate briefing, applicable
  acceptance/verification, predecessor references, and gate evidence before
  project work. A missing or unreadable required input produces one durable
  then-current durable-question operation, then the same worker polls its
  answer and reruns the full bootstrap validation before proceeding.
- Extend hook-trust/preflight and marketplace validation to the six-hook
  contract, and add lifecycle, prompt-contract, and trust regressions.

## [9.2.23] - 2026-08-21

This source-tree patch prevents a host-proven lost native worker from pinning
the Cortex task ledger until its lease expires.

- Treat only an exact targeted wait's identity-unavailable result as the
  existing terminal reportless-stop transition, then require the normal failed
  continuation before any corrective dispatch.
- Keep timeouts, transport/generic failures, ambiguous multi-target results,
  and unrelated identities non-terminal; no such result authorizes a worker
  replacement.
- Preserve the host diagnostic boundary: response text is neither persisted in
  lifecycle telemetry nor injected into coordinator context.
- Align the manifest cachebuster, source validators, and current-contract
  documentation with 9.2.23.

## [9.2.22] - 2026-08-21

This source-tree patch clarifies the public start-task contract without
weakening its fail-closed validation boundary.

- State in both the public MCP description and the bundled coordinator protocol
  that `task.verification` is the array of concrete authoritative checks, not a
  mode selector, and that `verification_mode` is not a task field.
- Keep unknown task fields rejected before reservation or task creation, and
  add contract parity coverage for the public schema, coordinator skill, and
  no-task-created negative path.
- Align the manifest cachebuster, source validators, release markers, and
  current-source documentation with 9.2.22.

## [9.2.21] - 2026-08-21

This source-tree patch introduces Prompt Contract Architecture v2 without
changing the orchestration lifecycle, dispatch artifact, digest, bootstrap, or
report transport contracts.

- Compile artifact-backed v3 worker briefings from one structured ownership
  matrix in the fixed authority → constraints → untrusted assignment JSON →
  role → conditional mode/gate/context → tool/output/stopping order.
- Keep dispatch-specific values inside a dynamically fenced JSON assignment
  boundary, retain the v2 expanded briefing only as a deprecated compatibility
  adapter with required-marker parity, and reject duplicate/unknown sections.
  Production orchestration is statically fail-closed to canonical v3; v2 is
  callable only by the explicit compatibility A/B baseline.
- Add deterministic source lint and fixture prompt-eval checks. Live prompt
  evaluation fails closed unless an explicit no-fallback `gpt-5.6-luna` high
  executor is supplied; the normal suite never calls a model.
- Add a separately opt-in v2 compatibility-adapter versus canonical-v3 A/B
  fixture. Offline checks report only digest/section/byte/data-boundary deltas;
  the live Codex runner reports deterministic structured-response, leakage,
  tool, routing, retry/replay/completion, output, token, and time metrics.
- Align source release markers, marketplace validation, and installer parity
  with 9.2.21.

## [9.2.20] - 2026-08-21

This source-tree patch closes the remaining human-boundary, dispatch-authority,
and release-marker gaps.

- Validate user-facing candidates before redaction, apply complete metadata and
  path removal in every communication profile, and keep localized natural,
  compact, technical, and neutral projections inside the public boundary.
- Keep plan and question views limited to safe user content, with profile-aware
  rendering and deterministic quality fallbacks; internal protocol data remains
  under `internal`.
- Reject public compact-wave requests for visible threads unless the immutable
  task contract carries explicit `visible_thread_requested=true`; hidden
  subagents remain the default and explicit visible-thread dispatches retain
  their existing host checks.
- Move release markers and validator expectations to 9.2.20 while preserving
  the complete 9.2.19 historical entry.

## [9.2.19] - 2026-08-21

This source-tree patch hardens bounded report intake and lifecycle-hook
snapshots without weakening ledger integrity.

- Prepare and validate worker report drafts outside the mutation lease, then
  recheck task revision, draft identity, and content digest in the final
  serialized commit. Lock acquisition is bounded; contention and stale
  preparation remain retryable and preserve the draft;
  `CORTEX_REPORT_PREPARE_COMMIT=off` retains the serialized fallback.
- Bound every ledger mutation lock by default and return structured
  `ledger_busy` diagnostics with non-secret holder metadata; expected
  contention is not written to the private tool-error log.
- Read lifecycle-hook state through a bounded read-only SQLite snapshot and keep
  optional telemetry writes fail-open when the database is busy.
- Preserve every user requirement and the latest steer in Planner feedback,
  require traceability coverage for revised tasks, and expose a single mutable
  task-local plan tracker with status, order, dependencies, gates, acceptance,
  and verification for every package and microtask.
- Add `natural`, `compact`, and `technical` user-communication profiles with
  separated internal metadata and quality checks for start, progress, plan
  approval, questions, errors, blockers, and completion; aggregate all report
  validation diagnostics in one safe retry response.
- Make `visible_output` the human-facing boundary and keep a bounded machine
  receipt/compatibility projection under `internal`; localize Russian and
  English lifecycle/plan messages, map `neutral` to `natural`, enforce quality
  fallbacks, keep worker waiting silent, and cover delegation/report
  persistence with a live smoke test.
- Preserve scalar task text as one atomic scope/requirement during task and
  dispatch materialization, reject malformed list items, and return all
  independent Planner identifier, dependency, and coverage-reference errors
  with exact draft paths in one retry.
- Require positive evidence for every passed gate, preserve failed checks for
  honest blocked/rework results, harden harvest evidence and feature-page
  validation, and prevent mutating task kinds or negated failure text from
  taking read-only or incorrect recovery routes.
- Rehydrate an exact worker's immutable assignment, compiled plan unit, and
  user-intent artifact after compaction/reset, failing closed on ambiguous
  identity or missing artifacts.
- Align the marketplace validator, installer, documentation, and invariant
  checks with the 9.2.19 source manifest.

## [9.2.18] - 2026-08-21

This source-tree patch closes report and corrective-orchestration liveness
gaps without weakening ledger integrity.

- Remove programmatic planner/dispatch briefing size rejection. Complete plans
  remain immutable digest-bound artifacts; briefings carry compact guidance and
  references rather than a second inline plan copy.
- Scope no-progress evidence and pauses to the failed gate. Unpaused siblings
  in the current parallel wave can continue, while later dependency waves do
  not leapfrog an unresolved paused gate.
- Require an explicit paused-gate target for multi-pause Planner-first
  recovery, preserving every other gate's pause and provenance.

## [9.2.17] - 2026-08-21

This source-tree patch makes bundled skill-read deduplication advisory and
context-aware. Repeated full reads of an unchanged Cortex skill in one context
are surfaced as a diagnostic without blocking the read; compaction or a new
context epoch permits the read again. Host-internal UI skill loads remain
outside the project-tool hook's accounting boundary.

## [9.2.16] - 2026-08-21

This source-tree patch closes three recovery and contract gaps:

- Coordinator recovery no longer asks a reviewer to retry an impossible
  resolution report.
- Review and close preserve honest `BLOCKED` markers while corrective work is
  still required.
- The `record_report` schema branch now matches the runtime validation branch.
- Report intake no longer truncates report strings or list items at the former
  64 KiB/100-item sanitization caps. Complete immutable report artifacts are
  bounded only by the explicit 8 MiB atomic artifact boundary; private report
  drafts allow 17 MiB for envelope headroom.
- Report reads return the complete immutable artifact through signed cursors;
  32 KiB is a transport-page bound only. Detailed plans are passed to workers
  by exact artifact ref/path/SHA-256 metadata in the briefing.
- The duplicate-read hook now recognizes installed Cortex `SKILL.md` files,
  which sit outside project roots, and gives a skill-specific same-context
  advisory without blocking valid retries or compaction reloads.

## [9.2.15] - 2026-08-20

This source-tree patch makes the public report boundary and external
control-plane routing recoverable without weakening ledger integrity.

- Reject malformed JSON scalar values at the public report boundary with a
  structured retryable validation result instead of leaking a Python exception
  through MCP; both direct and draft-backed reports retain the normal repair
  path.
- Route an explicitly external `codex://threads/...` ledger-continuation task
  without a requested project mutation around write-required implementation
  and QA gates, while retaining those gates for actual repository changes.
- Reject a requested inactive `record_gate` target with a retryable,
  non-mutating `gate_mismatch` response rather than silently advancing the
  first active gate.
- Treat an open P2 canonical finding as advisory unless its authoritative
  record explicitly sets `blocking=true`; P0/P1 and every explicitly blocking
  open finding still require rework.
- After a ledger passes the authoritative migration validator, use a bounded
  process-local, read-only readiness probe for ordinary opens rather than
  taking the migration lock and `BEGIN IMMEDIATE` every time.  The probe
  rechecks database identity, SQLite schema/user markers, and migration
  history; any drift falls back to the existing fail-closed full validator.

## [9.2.14] - 2026-08-20

This source-tree patch prevents an origin verifier from receiving a report
contract that is impossible to satisfy after multiple corrective routes.

- Retain every current, server-bound, passed corrective receipt for each active
  finding/origin binding through QA and security frontier compaction, rather
  than retaining an incomplete target-gate projection.
- Before dispatching an origin verifier, require every active closure-rework
  route to have its current corrective receipt. A missing receipt returns the
  recoverable `closure_rework_preflight_required` result before any worker or
  report draft is created.
- Cover two independent corrective routes through QA and security to a final
  Review whose first valid PASS-resolution is accepted, plus the no-dispatch
  missing-receipt path.

## [9.2.13] - 2026-08-20

This source-tree patch closes two fail-closed governance finding-route gaps.

- Do not synthesize a resolved `verification-required-missing` finding from a
  no-findings pass at a different gate. The canonical blocker remains open
  until its own fresh origin gate can validate the corrective route.
- Rework raised by `governance_activation` or `governance_close` now moves the
  originating governance verifier and every later closure verifier behind the
  corrective target, so a resolution receipt has the required fresh origin
  rerun instead of an orphaned provenance path.

## [9.2.12] - 2026-08-20

This source-tree patch preserves resolution evidence across multi-hop
corrective waves.

- Keep the exact server-bound corrective report in active rework handoffs
  through later QA/security acknowledgements, so the origin verifier receives
  it with the source finding and can issue a valid resolution receipt.
- Cover the Review → corrective worker → QA → final Review chain, including
  the case where ordinary transitive frontier compaction would otherwise drop
  the corrective receipt.

## [9.2.11] - 2026-08-20

This source-tree patch hardens the public worker-report boundary and hook
runtime loading without weakening report evidence or write-attribution checks.

- Keep coordinator and submission authority coordinator-only, and return a
  precise same-attempt correction when a worker sends them to
  `record_report`.
- Create and read report drafts through private descriptors: a draft must be a
  current-user, regular non-symlink file with exact `0600` mode, and a failed
  validation never repairs an arbitrary worker-authored path.
- Make missing evidence-marker and `changed_files`-baseline diagnostics
  directly actionable while retaining the exact evidence and attempt-delta
  integrity contract.
- Make lifecycle hooks resolve their bundled Python runtime when a host loads
  the hook with `importlib` instead of executing it from the scripts directory.

## [9.2.10] - 2026-08-20

This source-tree patch fixes the approval-state inconsistency that could occur
when generic or internal pipeline rework reopened the final `plan` gate.

- Retire the active required-plan approval atomically whenever a pipeline reset
  includes `plan`, while preserving the prior approval basis and request data
  in audit history.
- Recover legacy completion-pending replacement planners whose current report
  differs from a stale approved basis by opening a fresh, request-bound approval
  hold instead of preparing a successor or failing on the obsolete manifest.

## [9.2.9] - 2026-08-20

This source-tree patch fixes recovery after a native worker stops after it has
already recorded a report. The stopped child is no longer projected as a live
worker or retried automatically.

- Classify host-stopped `report_recorded` attempts as completion-pending and
  require the coordinator to explicitly select one receipt-attested report.
- Reject stale Planner report revisions before state mutation; when every
  stopped-report receipt is unusable, retain it for audit and require a fresh
  Planner-first recovery instead of leaving the task waiting indefinitely.

## [9.2.8] - 2026-08-20

This source-tree patch release extends the 9.2.7 recovery hardening line with
disaster-recovery backups that preserve and verify the governance lifecycle
authentication key alongside the SQLite ledger. Backup bundles are private,
atomically published, fingerprinted, and validated through a fresh host
projection before they are accepted as recoverable evidence.

- Include the governance lifecycle key and integrity manifest in private
  `.cortex-backup` bundles without returning or logging the key.
- Reject legacy bare SQLite backup files as insufficient for governance
  disaster recovery.

## [9.2.7] - 2026-08-20

This source-tree release extends the 9.2.6 hardening line with the P1.1
coordinator recovery delivery work. The source cachebuster is
`9.2.7+codex.20260820104507`; publication and installed-plugin parity remain
separate release gates.

- Preserve an idempotent pending recovery delivery across a lost transport
  response, with explicit acknowledgement before capability rotation becomes
  final.
- Keep recovery replay bound to the active coordinator identity and existing
  proof, so response redelivery cannot become an identifier-only capability
  lookup.

## [9.2.6] - 2026-08-20

This source-tree hardening candidate is not a publication or installation
claim. Its source cachebuster is `9.2.6+codex.20260820093505`; tracked-archive,
remote, and installed-plugin parity remain separate release gates.

- Add promotion replay lookup by deterministic policy reference, avoiding
  pagination-bound false corruption after a large governance history.
- Add governance schema v12 integrity: immutable artifact-authoritative record
  bodies, exact normalized scope, linear revision chains, strict JSON,
  immutable-field triggers, conflict-safe submission receipts, and host-keyed
  authentication for the complete governance lifecycle envelope.
- Bind coordinator capabilities to task/initiative scope, principal, thread,
  generation, expiry, allowed actions, and revocation; rotate a lost bearer
  only for the same active identity without persisting plaintext.
- Add a no-progress circuit breaker that pauses materially identical corrective
  work for an explicit user strategy without creating a false pass.
- Make semantic steer impact and worker questions revision/generation-aware,
  superseding stale questions and downstream evidence.
- Bound manifest capture by entries, hashed bytes, and elapsed time, reuse a
  bounded digest cache, and require the 50,000-file benchmark in CI.
- Add CI timeout/concurrency cancellation and explicit CODEOWNERS review
  coverage while retaining the Python 3.11/3.12 validation matrix.

## [9.2.4] - 2026-08-19

- Preserve integer relative steps after the server inserts or reorders
  `governance_activation` and `governance_close`, allowing the public
  `continue_orchestration` contract to advance automatically governed tasks.
- Project governance reviewer reports into server-owned, scoped, immutable
  evidence that binds the consumed report receipt, verified execution,
  independent reviewer identity, and every required governance obligation.
- Require canonical `gate_result` envelopes for governance activation and
  close reviews, and route their canonical blockers through the same bounded
  corrective-review machinery as ordinary review and close gates.
- Add deterministic and live C3 fixtures that omit `governance_mode`, prove
  automatic `full` resolution from complexity, execute both governance review
  waves, validate typed immutable evidence, and require final handoff.
- Align immutable governance briefings and the live validator on the canonical
  `gate_result` envelope, and use a neutral text result in the governance live
  fixture so documentation policy does not create unrelated rework.

## [9.2.3] - 2026-08-19

- Store only a digest of the coordinator governance capability, issue the raw
  bearer once, refuse replay reissuance, and scrub/invalidate legacy plaintext
  capability fields on first registry access.
- Make `governance_mode=minimal` request the lowest governed baseline while
  promoting it to the server-required depth for complexity and risk triggers.
- Bind independent initiative-close review to a passed `code_reviewer`
  `governance_close` attempt, its report reference, and a completed native
  worker session instead of caller-authored reviewer fields.
- Enforce sensitive-record retention and access policy at write time, including
  derived/bounded expiry and optional allowed/redacted field rules, while
  retaining expired rows in append-only audit history.
- Preserve an approved policy while a worker replacement is pending, reject
  conflicting initiative replays across every immutable creation field, and
  support reviewed project-scope promotion into project policy.
- Count a closure rework iteration as the next attempt ordinal rather than an
  extra prior failure, so first/second/later corrective dispatches reliably use
  `high`/`xhigh`/`max` effort without premature escalation.
- Remove the pipeline, QA, review, and same-strategy attempt caps. Corrective
  work now remains unbounded while acceptance or findings require it, raises
  effort through `high`/`xhigh`/`max`, and selects Terra for eligible ordinary
  work after two prior failures.

## [9.2.2] - 2026-08-19

- Treat `replan_count` as audit history and retain `replan_limit` only as
  compatibility metadata, so each new evidence-backed review finding can open
  another corrective pipeline regardless of task length.
- Preflight material future-wave approval, rework, and obligation rules before
  recording attempts or gates, preventing rejected replans from leaving an
  active Planner gate with approved stale state and no dispatch.
- Recover legacy stranded active tasks through one safe Planner-first resume
  payload when no worker is live or pending, and extend the black-box JSON-RPC
  smoke to prove three replans pass despite a persisted legacy limit of two.
- Make ignored side effects framework-independent at read-only gates: all are
  non-blocking and digest-audited, while recognized caches such as `.expo`
  receive an additional ephemeral classification and unknown future-framework
  outputs remain visible as unclassified receipts.
- Add an optional localized free-form field beside every native and fallback
  plan-approval choice; non-empty text durably requests Planner revision and
  becomes the replacement plan's feedback instead of silently approving.
- Make report-link publication an at-most-once completion event: only the first
  full coordinator read after the matching native worker stop returns a link,
  and the same user-facing message must summarize what completed and what
  happens next. Early reads and rereads remain link-free.

## [9.2.1] - 2026-08-19

- Preserve immutable pipeline obligations across context compaction and future-wave
  reassessment, and reject any replacement that silently drops pending
  implementation work without consuming a recovery attempt.
- Infer rework at the public facade and atomically recover exhausted closure
  routes through a freshly approved Planner-first delivery graph. Missing
  implementation now restores its QA, audit, review, documentation, and close
  successors instead of dispatching another writer.
- Check accepted planning catalogs against verified delivery attempts before
  documentation or close, and extend the black-box JSON-RPC smoke with a
  dynamic-replan proof that implementation cannot disappear.

## [9.2.0] - 2026-08-19

- Remove task-wide report-count and aggregate-byte quotas; reports remain
  individually bounded and SQLite-backed, so long-running tasks may retain
  thousands of immutable handoffs. Successor dispatches now receive the
  verified transitive DAG frontier: every omitted report is covered by a
  passed report that durably acknowledged it, while full history and the
  Planner evidence digest remain intact.
- Attach a task-wide `resolved_user_decisions` snapshot to every immutable
  report, and forbid successors from asking an equivalent resolved question
  under new wording, keys, phases, or attempts unless the user reopens it.
- Historical structured-choice rendering was superseded by the current durable
  plain-text question and answer contract.

## [9.1.1] - 2026-08-19

- Historical question presentation was superseded by one arbitrary-Unicode
  plain-text question blob and one arbitrary-Unicode plain-text answer blob.

## [9.1.0] - 2026-08-19

- Make orchestration and every supporting overlay explicitly opt-in. The
  orchestrator keeps invocation, harvest routing, isolation, and team policy;
  `cortex-control` is now the single coordinator state-machine and runtime core.
- Compile Worker Briefing v2 from fixed authority/protocol sections, one
  selected-role playbook, conditional mode and phase overlays, and a
  JSON-serialized untrusted Assignment data block. Remove worker-irrelevant
  model/effort and manifest-baseline metadata, duplicated team/profile policy,
  and raw task-value interpolation.
- Move harvest-only specialization out of ordinary agent TOMLs into validated
  conditional overlays, narrow harvest detection to explicit harvest tokens,
  fix discovery dependencies to begin at Scope, and remove obsolete normative
  history from the harvest skill.
- Split automatic recovery into `same_strategy_limit=2` and
  `phase_attempt_limit=3`; a third phase attempt now requires a materially
  different `next_strategy` or a future-wave replan. Add prompt-duplication,
  prompt-injection, overlay-isolation, description, and representative byte-
  budget regression checks.
- Rename the misleading `token-monitoring` skill to `progress-accounting`; its
  policy still forbids collecting token counts, hidden reasoning, or private
  telemetry.

## [9.0.4] - 2026-08-19

- Historical localized structured-question presentation was superseded by the
  current plain-text durable-question contract.
- Recover a missing matching parent-session binding from the task's durable
  parent session when a worker stops, while rejecting every different session.
  Every normal stop-hook return now includes an explicit `outcome`.
- Keep deterministic Luna-high verification fixtures explicitly task-scoped,
  so their `continue_orchestration` and resume calls cannot fail closed after
  the task-ref safety change.

## [9.0.3] - 2026-08-19

- Fail a new start closed when the then-current MCP catalog is incompatible: the
  result is non-retryable and creates neither a task nor recoverable task
  authority, so a coordinator cannot recover an unrelated older task.
- Require the exact task authority from a successful lifecycle response for every
  task-scoped continue, management, recovery, and report-read call. Cortex no
  longer falls back to a project-wide active task.
- Give every internal worker profile a turn-local read discipline: reuse a
  fully read skill, briefing, source, or report within the turn, reopening it
  only for pagination, an intervening edit, or a distinct unread range.
- Make the final Planner's verified scope/discovery/design report basis
  server-owned, so a compact future-wave request cannot accidentally omit it.
  If a later future-wave validation fails after gate recording, the same
  step/results may retry with corrected `future_waves`, `reason`, or `rework`
  instead of being trapped behind the rejected payload's idempotency receipt.

## [9.0.2] - 2026-08-18

- Reject system and home directories as `project_root` before Cortex begins its
  recursive content-addressed manifest capture. A project root must be a
  specific repository or worktree, preventing an oversized synchronous MCP
  request from appearing hung.
- Historical note: question-answer protocol behavior was revised in this
  release. The current contract preserves submitted Unicode answer text
  directly and does not expose a translation lifecycle. Coordinator guidance
  explicitly forbids searching plugin source/cache or runtime internals to
  infer public arguments.

## [9.0.1] - 2026-08-18

- Let read-only result gates retain recognized cross-language test, build, and
  cache residue as auditable receipt data instead of rejecting a valid report
  and retrying the same worker. The bounded manifest policy covers conventional
  generated directories, roots, files, bytecode, virtual environments, and
  build outputs, including the common forms that projects put in `.gitignore`.
  Unknown ignored artifacts remain hard failures.

## [9.0.0] - 2026-08-18

- Consolidate report draft validation and persistence in `record_report`.
  Workers now fill the private template and record it directly; callers that
  cannot edit the draft may submit a merge patch or complete replacement.
  Invalid records retain the draft for retry without consuming an attempt, and
  successful records revalidate, atomically persist, then delete it.
- Remove the public `validate_report_draft` operation and `validation_digest`;
  the public contract is now `cortex/orchestration/v5` with exactly eight tools.

All notable public changes to Cortex are recorded here. Release entries use
semantic versions; the plugin manifest adds a unique Codex cachebuster to the
same base version.

## [8.1.2] - 2026-08-18

- Keep caller/input/schema failures from every public worker tool retryable on
  the same worker attempt without consuming the three-attempt recovery budget;
  only explicit non-retryable integrity, storage, permission, or unavailable-
  identity failures end the worker.
- Normalize oversized dispatch-briefing, worker-report, and coordinator
  artifact `max_bytes` requests to the safe 32768-byte SQLite transport bound
  and continue through opaque cursors instead of returning an MCP error.
- Return structured field diagnostics from the then-current worker-question
  operation, `get_report_template`, and `read_worker_report`, and recover report numbering
  from the immutable artifact catalog so interrupted index writes do not reuse
  an export path.
- Bind required plan approval to an opaque request ID and expose the pending
  plan through the canonical Approve/Cancel interaction before dispatch.

## [8.1.1] - 2026-08-18

- Make `get_report_template` create one private, fully structured temporary
  JSON report file before validation and return only its `draft_ref`, absolute
  `draft_path`, expiry, and required sections instead of echoing the template.
- Let workers edit that same file directly or apply bounded JSON Merge Patches
  through `validate_report_draft`. Invalid validation keeps the file and never
  consumes the three-failed-attempt recovery budget; success binds a digest to
  the same file without returning its body.
- Make `record_report` reload and atomically revalidate that exact file from
  only worker authority, `draft_ref`, and `validation_digest`, then delete the
  draft and its metadata after the durable report transaction commits. Drafts
  expire after one hour and a new template supersedes the prior attempt draft.
- Treat ordinary source deltas observed by a host-sandboxed read-only worker
  as concurrent shared-workspace evidence instead of an impossible report-JSON
  correction. Claimed `changed_files` and generated or ignored side effects
  remain hard failures for read-only gates.

## [8.1.0] - 2026-08-18

- Add side-effect-free `get_report_template` and `validate_report_draft` worker
  tools. Draft validation returns field paths, concrete fixes, and a digest for
  one unchanged atomically persisted `record_report` payload without consuming
  the three-failed-attempt recovery budget.
- Keep revised Planner reports collision-free by storing every overview under
  its immutable `planning/revisions/plan-<report-ref>/overview.md` revision;
  `planning_current` remains the current-plan pointer.
- Accept globally unique cross-package microtask dependencies while preserving
  unknown-reference and whole-plan cycle rejection.
- Accept concise, non-empty executed-check evidence instead of requiring an
  arbitrary minimum word count that could discard valid QA reports.

## [8.0.0] - 2026-08-18

- Separate evidence-first `scope` from the final `plan`; C2 starts with
  Explorer discovery, while C3 and harvest start with structured Planner
  scoping. Architecture, database architecture, and UX precede the final
  Planner; security, performance, and accessibility remain post-implementation
  audits.
- Bind required plan approval to the plan revision, planner report, verified
  predecessor digest, and semantic future-pipeline digest. Material replanning
  preserves approval history and requires fresh approval; no-op and
  transport-only changes do not invalidate approval.
- Keep the strict seven-field `cortex/report/v1`, v4 public surface, and
  SQLite v8 ledger while adding pipeline contract v2 with v1 resume behavior.
- Make the launcher compatible with stock macOS Bash 3.2, cap sensitive tool
  error logs at 10 MiB with tail-preserving rotation, and require root UI
  questions to use the user's language.
- Precompute Codebase Memory project keys from canonical project roots in every
  worker briefing; routine queries no longer spend a call on `list_projects`,
  which remains a single exact-root fallback for lookup drift or collisions.
- Move runtime-only coordination, ownership, verification, and private-log
  handling rules into the bundled orchestrator/control skills; root
  `AGENTS.md` now contains repository-development policy only.
- This source candidate has not installed or updated a user plugin and has not
  been published, committed, or tagged.

## [7.1.2] - 2026-08-18

- Align the worker report contract across runtime prompts, validators, and
  documentation: `cortex/report/v1` now has exactly seven ordered fields,
  `summary`, `findings`, `questions`, `changed_files`, `tests`, `evidence`,
  and `uncertainty`. `gate_result` and `closure` remain separate top-level
  compatibility siblings.
- Harden report reconciliation, JSON-RPC harness cleanup, Python 3.11
  preflight isolation, and release/version invariants.
- Restore complete Python 3.11/3.12 discovery and offline release gates in CI,
  including marketplace, AST, shell, cold-boot, deterministic fixtures,
  benchmark, conditional fresh-plugin, and tracked-archive checks.
- Source/package evidence for `7.1.2+codex.20260818103113` includes 476-test
  full discovery on Python 3.11 and 3.12, focused `ResourceWarning` coverage,
  cold boot on both supported versions, deterministic fixtures, benchmark, and
  an isolated fresh-plugin probe. Three source-mode live scenarios passed; the
  planner lifecycle completed but its deterministic two-package live rerun is
  still pending, so a complete live PASS is not claimed. The installed user
  plugin remains 6.6.0 and was not changed.

## [1.0.6] - 2026-08-14

- Apply the exact model/effort remapping table in the runtime, including Sol
  routes, and persist the remap metadata in each delegation.
- When the hidden `spawn_agent` catalog lacks Luna but `create_thread` exposes
  it, create a visible Luna task by default instead of silently dispatching a
  Terra subagent. Keep Terra only as an explicit compatibility opt-out.
- Add regression coverage and synchronize routing, fallback, and release
  documentation with the runtime contract.

## [1.0.3] - 2026-08-14

- Add server-level MCP approval guidance and document the token cost of
  automatic approval review.
- Keep visible threads in the saved Local checkout by default, with an
  explicit worktree option for isolation.
- Document hidden `spawn_agent` dispatch versus user-visible `create_thread`
  dispatch and the Luna/Terra fallback trade-off.

## [1.0.1] - 2026-08-14

- Package Cortex as one root marketplace backed by `plugins/cortex`.
- Provide the opt-in Cortex task, gate, report, evidence, and handoff control
  plane with 21 agent profiles and 10 skills.
- Add isolated installation, cold-boot, tracked-archive, redaction, symlink,
  and runtime-state boundary checks.
- Make upgrade backups collision-safe and private.

This entry describes repository readiness; it does not claim that a tag,
remote release, or official catalog listing exists.
