# Cortex orchestration feature parity

## Purpose and non-negotiable preservation rule

This document is the capability inventory for the Cortex 1.12.1 architecture
recovery. It maps behavior currently specified by bundled skills, profiles,
runtime, schemas, projections, documentation, and tests to the proposed
Domain Kernel, semantic registry, coordinator policy, and read projections.

The redesign is an internal ownership change, not a feature reduction. Every
capability listed here must remain observable after migration. The coordinator
and workers remain model-owned where the current contract says they are
model-owned; the Kernel adds durable validation and atomic state, but does not
turn orchestration into an autonomous backend workflow.

Status values:

- **Present** — current behavior is represented and has an identifiable owner.
- **Partial** — some runtime support exists, but policy, adapters, or legacy
  paths still carry essential behavior.
- **At risk** — behavior is primarily skill/prompt convention or is not yet
  covered by the proposed runtime boundary.
- **Target** — required destination for the architecture migration.

## Ownership model

| Boundary | Responsibility that must remain there |
| --- | --- |
| Coordinator policy | Opt-in routing, DAG proposal/adaptation, worker selection, model/effort selection, governance judgment, user questions, steering, recovery/rework choice, evidence sufficiency, and final synthesis. |
| Domain Kernel | Aggregate identity, authorization, ownership, typed handle scope, legal state transitions, dependency validity, evidence manifests/receipts, publication completeness, logical slots, replay/conflict behavior, initiative deduplication, closure readiness, and durable command receipts. |
| Semantic registry | One source for operations, schemas, typed handles, producer/consumer edges, role-specific envelopes, errors, and generated validators/catalogue. |
| Ledger/storage | Append-only historical evidence, migrations, physical report chunking, indexes, and transactional persistence. Storage details are not the model-facing protocol. |
| Projections | Derived task, plan, report, timeline, initiative, and human-readable views. Projections are never authority or recovery state. |
| Live-dev transport | Default-server tmux and ordinary Codex transport only. It inserts literal text and keys and exposes observations; it never answers, approves, parses acceptance, or decides pass/fail. |

## Capability parity matrix

| Capability | Current owner | Target owner | Migration status | Acceptance test | Preservation status |
| --- | --- | --- | --- | --- | --- |
| Explicit Cortex opt-in; normal/help/harvest route separation | Skills and coordinator | Coordinator route policy + task route metadata | Target | Normal task bypasses Cortex; explicit route records the selected route exactly once. | Must preserve |
| English-only worker, inter-worker, report, and ledger boundary | Skills, worker message, profiles | Coordinator policy + worker-message compiler + Kernel metadata | Partial | User-facing localization may differ, but worker messages and durable reports remain English. | Must preserve |
| Worker-only project execution | Orchestrator/control skills | Coordinator policy + assignment authorization | Partial | Coordinator cannot read or mutate project state; every project action is worker-owned. | Must preserve |
| At least one worker for project-facing work | Orchestrator skill | Task aggregate invariant | Target | Project task cannot close without a worker; non-project answer may use no worker. | Must preserve |
| Dynamic model-owned DAG | Orchestrator/adaptive skills | Pipeline aggregate + coordinator adaptation | At risk | Evidence appends a DAG revision; completed nodes remain immutable and unstarted nodes can be adapted. | Must preserve |
| DAG node lifecycle and dependency validity | Orchestrator | Domain Kernel state machine | Target | Invalid transitions are rejected; valid transitions replay safely across all declared states. | Must preserve |
| Parallel waves and non-overlap ownership | Lanes docs, orchestrator, coordinator | Kernel resource claims + coordinator scheduler | Partial | Overlapping active mutation scopes are rejected; disjoint scopes may run in parallel. | Must preserve |
| Parent-linked retry, recovery, replacement, and rework | Orchestrator/adaptive/context skills | Assignment lineage + coordinator recovery policy | Partial | Replacement retains parent evidence; retry does not create an unrelated task; rework requires material change. | Must preserve |
| Same-worker liveness/checkpoints | Orchestrator/adaptive skills | Coordinator policy with durable liveness evidence | Present | Silence never proves failure; bounded checkpoint/reconcile precedes replacement. | Must preserve |
| Planner discovery and durable plan | Planner profile, orchestrator, output validation | Planner assignment + immutable plan publication | Partial | Planner is read-only; complete plan is immutable and linked to the task contract. | Must preserve |
| Immutable plan revisions | Orchestrator, governance docs, projections | Plan aggregate/publication | Partial | Revision appends a new digest and does not overwrite earlier plan evidence. | Must preserve |
| Plan approval gate | Orchestrator, decision/governance skills | Decision aggregate + coordinator approval policy | Partial | Required downstream work cannot proceed without explicit approval evidence. | Must preserve |
| Clarification questions and answer recording | Decision code, orchestrator, context skill | Clarification Hold aggregate + decision aggregate + semantic registry + coordinator continuation policy | Partial | Same logical question returns one pending hold/binding; the answer consumes it once. With no supported host callback, only the first accepted publication from that exact assignment reconciles continuation; otherwise recovery remains coordinator-owned. | Must preserve |
| Same-task steering | Stage 09 docs and orchestrator | Decision aggregate + host steering adapter | **Blocked** | Supported host steers the exact live task through a server-issued continuation capability; unsupported/ambiguous continuation reports the limitation and uses evidence-backed rework. | Must preserve |
| Exact typed evidence handoff | Worker message, orchestrator, report skills | Typed capabilities + assignment evidence manifest | Partial | Producer handle is passed byte-for-byte to every declared consumer; wrong type/scope is rejected. | Must preserve |
| Evidence read receipts | Store, worker policy, documentation/output skills | Kernel evidence-consumption transaction | Partial | Declared predecessor evidence must be consumed before result/publication. | Must preserve |
| Worker-owned report submission | Orchestrator and worker message | Assignment authorization | Partial | Coordinator cannot submit for a worker; worker can publish only its own assignment. | Must preserve |
| Complete role-specific reports | Output validation, profiles, presenters | Registry-generated publication schemas + Kernel validators | Partial | Incomplete plan/result/verification/documentation envelope creates zero writes and names missing obligations. | Must preserve |
| One terminal result per assignment | Store guard and orchestrator policy | Publication logical slot | Partial | Second normal result conflicts; explicit parent-linked correction is distinct. | Must preserve |
| Internal report chunking and historical evidence | Store, presenters, projections | Ledger/storage implementation | Present | Atomic public publication still preserves historical chunks and digest-stable rendering. | Must preserve |
| Per-delegation model and reasoning routing | Model routing, profiles, orchestration docs | Coordinator routing + assignment receipt metadata | Present | Independent assignments retain independent supported model/effort pairs and role restrictions. | Must preserve |
| Profile specialization | All bundled agent profiles | Profile registry + assignment policy | Present | Every existing profile remains selectable under its role boundary and completion contract. | Must preserve |
| C1/C2/C3 and minimal/light/full governance | Orchestrator/adaptive/output/governance skills | Governance aggregate + coordinator policy | Partial | Evidence can revise governance level; governance remains advisory and does not block safe work by itself. | Must preserve |
| Advisory governance nonblocking guarantee | Governance and progress skills | Coordinator policy; Kernel stores evidence | Present | Governance outage cannot authorize unsafe work or prevent an honest user-facing answer. | Must preserve |
| Initiatives and cross-task dependencies | Governance skill/docs and projections | Initiative aggregate + dependency graph | Partial | Cross-task lineage is explicit; dependency changes are durable and scoped. | Must preserve |
| Initiative materiality and deduplication | Governance policy, current store | Kernel logical initiative slot | At risk | Repeated stage progress does not create new initiative revisions; material goal/dependency change does. | Must preserve |
| Documentation impact decision | Documentation-sync, output validation, orchestrator | Documentation obligation projection + coordinator policy | Partial | Verification cannot close before documentation-impact outcome is recorded. | Must preserve |
| Documentation sync and independent verification | Documentation-sync, writer profile | Documentation assignments + publications | Partial | Material documentation change is written and independently verified; no-impact has an explicit rationale. | Must preserve |
| Knowledge route and harvest/refresh | Knowledge-harvest/orchestrator skills and route docs | Closed route compiler + harvest pipeline | At risk | Explicit harvest runs bounded census, synthesis, write, and verification; ordinary tasks do not harvest. | Must preserve |
| Security/content safety and sensitive-data handling | Content-safety, control, security profile | Classification/redaction boundary + coordinator/worker policy | Partial | Secrets and raw private diagnostics do not enter prompts, reports, errors, projections, or commits. | Must preserve |
| Context compaction and recovery | Context-compaction skill | Durable task/pipeline snapshot + recovery policy | At risk | Resume uses exact task, DAG, bindings, workers, evidence, and project root without reconstructed identifiers. | Must preserve |
| Historical evidence and forward-only migrations | Store, migration code, compatibility tests | Ledger/history subsystem | Present | Existing databases migrate forward with data intact; historical rows remain readable. | Must preserve |
| Concurrency and replay semantics | Store idempotency and tests | Kernel command receipts/logical slots + SQLite-owned sidecar lifecycle | Partial | Identical concurrent commands produce one state and replay; changed payload conflicts; exit-code-aware two-process WAL/SHM stress shows no crash or hidden storage failure. | Must preserve |
| Lost-response reconciliation | Orchestrator/context/recovery skills | Receipt query + coordinator reconciliation policy | At risk | Commit-before-response loss is resolved read-only; no new binding or duplicate mutation is created. | Must preserve |
| Server-owned handles and decision identity | Decision code, worker policy | Typed capability types + Kernel identity | Partial | Same task/subject/prompt/revision returns one pending binding; stale/conflict states are explicit. | Must preserve |
| Atomic publication and zero-write incomplete behavior | Store/publication adapter | Kernel publication transaction | Partial | Failed completeness validation creates no publication/chunk/terminal assignment state. | Must preserve |
| Governance and closure readiness | Governance/validation/progress skills | Closure aggregate + Kernel evaluator | Partial | Closure derives missing approvals, assignments, evidence, docs, decisions, blockers, and risks from durable state. | Must preserve |
| Unresolved risks and follow-ups | Output validation, governance, closure | Closure aggregate + report/projection | Present | Unresolved risks remain explicit and prevent a falsely complete verdict. | Must preserve |
| Task/progress/timeline/human-readable projections | `v12_projections.py`, progress and view docs | Read-model projection subsystem | Present | Projections regenerate from ledger, detect stale/external edits, and never become authority. | Must preserve |
| Worker hidden-error visibility | Live-dev policy, worker handoff tests | Sanitized isolated MCP event journal + LLM verifier | Partial | Every terminal MCP tool-call wire outcome, including malformed call validation and physical wire fallback, is observed once with no raw caller values. The LLM reads the exact-session bounded journal; hidden error or unexplained replay fails live acceptance and a final report reference alone is insufficient. Candidate/live evidence remains open. | Must preserve |
| Real LLM-driven live-dev | Live-dev scripts/docs/AGENTS | Transport helper + coordinator verification policy | Present | Default-server tmux runs ordinary Codex; LLM reads pane/events and owns question, approval, and acceptance decisions. | Must preserve |
| Isolated candidate provenance | `cortex-dev`, provenance tests, packaging docs | Immutable candidate builder + runtime identity + isolated marketplace reconciliation + authoritative installed-candidate receipt | Partial | Source/candidate parity is proven before Codex starts from the exact stamped path written by successful isolated sync; a missing/tampered/foreign/symlinked receipt fails closed; stale candidate marketplace registrations converge only inside the exact non-symlinked isolated target; stable profile and unrelated isolated entries are untouched. | Must preserve |
| Single MCP server and complete catalogue | `cortex.py`, public contracts | Generated semantic registry + MCP adapter | Partial | One stdio server lists the complete semantic catalogue on first request. | Must preserve |
| Schema authority and no MCP parameters in skills/prompts | AGENTS, public contracts, live-doc tests | Registry-generated schemas/descriptions | Partial | Prompt/skill lint rejects MCP argument hints; first-call tests validate every advertised tool. | Must preserve |
| Package, hook, profile, and marketplace validation | Packaging docs/tests and generated cache | Registry/package release gate + native isolated marketplace reconciliation | Partial | Package gate rejects drift in tools, schemas, profiles, hooks, and bundled runtime; the native isolated marketplace entry converges from stale/missing states without direct config edits. | Must preserve |

## Current architecture gaps

The following facts must be treated as migration blockers rather than patched
individually:

1. `domain_api.py` still delegates most semantic operations to legacy
   `v12_service` and `V12Store`; the Domain Kernel does not yet own the full
   transition boundary.
2. There is no single declarative semantic registry generating the catalogue,
   schemas, handle edges, validators, and tests.
3. Dynamic DAG, governance adaptation, initiative materiality, knowledge-route
   selection, same-task steering, context recovery, and hidden-event review
   remain primarily coordinator/skill behavior.
4. The public semantic surface hides some internal lifecycle behavior, but the
   architecture must preserve historical report/chunk evidence and migrations.
5. Candidate provenance and black-box stdio qualification must precede any
   conclusion drawn from live-dev.
6. The live clarification failure proves the current binding-only decision API
   has no durable question hold, answer-delivery, exact-worker continuation, or
   worker-event capture boundary. This is a single architectural blocker;
   see [Phase D live decision root cause](phase-d-live-decision-root-cause.md).

## At-risk capabilities requiring explicit design gates

The highest-risk items are dynamic DAG adaptation, same-task steering,
initiative deduplication, lost-response reconciliation, context recovery,
knowledge harvest, and complete worker hidden-error inspection. They are at
risk because current correctness depends heavily on model instructions rather
than durable runtime invariants. None may be removed or silently converted
into a backend-only workflow.

## Required migration gates

The architecture migration is complete only when all of the following pass:

1. The semantic registry generates the complete single-server catalogue,
   schemas, typed handle edges, role-specific envelopes, and first-call tests.
2. Candidate source and isolated runtime are content-addressed and byte
   identical before Codex starts.
3. Decision, assignment/evidence, publication, governance, initiative, and
   closure vertical slices pass black-box stdio tests with durable-state checks.
4. Every mutation has an atomic logical slot and command receipt with exact
   replay/conflict semantics.
5. Existing historical data survives forward-only migrations without reset.
6. The full planner → approval → implementation → independent verification →
   documentation-impact → closure lifecycle retains all worker, governance,
   projection, security, knowledge, and recovery behavior.
7. Real LLM-driven live-dev runs inspect coordinator and bounded worker events,
   report zero tool errors and unexplained replays, and clean up only the exact
   named session.

This document is an architecture parity contract. A feature is not considered
preserved merely because a similarly named tool or test exists; its ownership,
durability, failure semantics, and acceptance evidence must also remain intact.

## D-CAND-006 sidecar-lifecycle status

The concurrency capability remains **Partial**. The historical SIGBUS is a
storage-lifecycle defect, not an acceptable replay outcome: Cortex had
recreated SQLite-owned WAL/SHM placeholders after close. The required target
is SQLite-owned live sidecars, descriptor-only startup validation, no routine
sidecar mutation/deletion, and process-safe teardown only after child
exclusivity is proved. One bounded source reproduction of 80 simultaneous
two-process opens completed cleanly with explicit exit-code capture, but this
is not sufficient to promote the feature. Exact-candidate and live evidence
remain blocked. See [Phase D SQLite SIGBUS root cause](phase-d-sqlite-sigbus-root-cause.md).
