# Typed orchestration integrity contract

## Status and purpose

### User-directed revision: obligation-preserving recovery

The unchanged source message is retained in
[obligation-preserving-request.ru.txt](obligation-preserving-request.ru.txt).
The numbered registry below is its interpretation, not a replacement source.

The latest user request supersedes the former fixed twenty-operation, nested
publication interface and error-free-only live acceptance below. Historical
implementation details and test totals remain evidence of previous work, not
qualification of this revision. No compatibility interface will be retained.

The revised implementation has these independently tracked obligations:

1. Preserve the original request byte-for-byte. Append direct user changes as
   immutable source events, never replace the original with a summary or plan.
2. Maintain versioned requirements and completion criteria independently of plan
   revisions. Link each requirement to its source event and exact source excerpt.
   Independently audit source-to-registry completeness: structural coverage alone
   cannot establish semantic completeness.
3. Only authenticated direct user changes may retire or modify obligations or
   criteria. Failures, retries, remediation and replanning leave the effective
   registry unchanged. Plan approval cannot silently authorize deletion.
4. Replace monolithic tool inputs with small transactional operations and simple
   scalar parameters/server-issued selections. Do not hide nested payloads inside
   JSON strings, Markdown or files. The server supplies known context and owns
   worker, assignment, revision and allowed-result binding.
5. Markdown reports communicate findings but never prove completion. Separate
   verification records bind criteria to observed checks, evidence provenance and
   implementation generation. A model success statement alone is insufficient;
   independent audits validate interpretation.
6. Deterministically enforce dependencies, readiness, ownership, freshness,
   transitions and coverage. The LLM chooses decomposition, specialists, ready
   parallel work, interpretation and repairs. The backend is not a scheduler.
7. Repair incomplete/failed work internally without changing approved obligations.
   Bound identical retries and require an evidenced strategy change after repeated
   failures. Exhausting one strategy never discards requirements or authorizes
   false closure. Pursue another safe in-scope approach without asking permission.
8. Ask only for necessary access, material branches, high-risk or explicitly
   requested plan approval, and fresh mandatory post-result closure review.
9. Successful closure requires all effective requirements and mandatory checks
   satisfied by current implementation evidence plus explicit user permission to
   close that exact result. An incomplete report is not successful task closure.
10. Remove legacy. Test every current tool and all 22 profiles locally, then real
    CLI/Desktop with parallelism, repeated steering, resume and deliberate faults.
    Final large live must demonstrate recovery, preservation of obligations, no
    false completion and no premature dispatch.

Declare fault injections and expected outcomes before each run. Record the actual
failure and recovery, invariant obligation registry (except direct steering),
current evidence and closure gate. Never weaken validation to accommodate a run.
A corrected retry is not clean first-call evidence, but may prove recovery in a
declared fault scenario. Unexplained errors/replays or unverified recovery fail
qualification. Report happy-path and fault-recovery evidence separately.

Migration order: obligation ledger and invariants; small operations/server
binding; recovery/closure integration; local tool/profile tests; short real CLI;
full CLI; unchanged-payload Desktop. Existing test totals do not qualify the new
interface. Update the Completion checklist after each verified step.

#### Host capability and adapter boundary

The host probe records declared, configured, observed, unsupported and unverified
capabilities separately. Missing observations never mean unsupported. Snapshots
bind host/app/engine identity, payload, catalogue, relevant configuration and
connection generation; changed identity invalidates previous evidence. Configured
capacity, available slots and observed concurrency are different facts. Luna uses
the configured default route without an explicit model; model-specific effort
must be supported and never exceed max.

CLI and Desktop adapters normalize native observations through an explicitly
supplied host transport. They do not invent a Python-to-native-agent API, choose
workers, retry ambiguous spawns or infer task completion. Send acknowledgement is
not receipt, wait timeout is not loss, and interrupt acknowledgement is not
quiescence. Input ordering, direct-user origin and resume/compaction each need
independent host evidence. The integrity core consumes these neutral facts, not
host-name branches. Passive preflight snapshots are not live qualification.

Initial source boundary implementation supplies an injected transport interface.
Both isolated launchers capture a passive snapshot before starting the host;
real native transport bindings and actual launch/session observations remain
required. Launch identity is not a verified native connection identity.

The CLI native-list hook delivers a JSON-text result with tagged completed
statuses. The observer bounds and decodes that envelope, discards terminal
report text and signs only normalized presence facts. A fresh complete list's
interrupted status represents an aborted turn; unknown states remain present.
Completion or confirmed abortion of a native turn can establish quiescence,
never fulfillment of product requirements; malformed or ambiguous projections
cannot clear a reconciliation barrier.

#### Source authority and small-operation implementation boundary

Source capture must occur at the host input boundary, not by accepting an
assistant-authored assertion that text came from a user. The documented
[UserPromptSubmit event](https://learn.chatgpt.com/docs/hooks#userpromptsubmit)
exposes submitted text, session and turn identity. Its actual delivery must be
qualified on both supported hosts before it becomes an admission dependency.
Do not parse the private transcript as a stable replacement API. Hook-generated
continuations and worker prompts must not become user-change authority; a host
submission receipt alone must not be mislabeled as proof of human authorship.
Missing source authority is an explicitly unverified transition, never permission
to reconstruct it from conversational memory.

The first task call should consume a server-issued source-submission anchor,
not recopy the request or its extracted outcomes. The trusted input observer
stores the exact source privately and exposes only the opaque bootstrap anchor.
The corresponding host call binds that anchor to the actual root session;
copying it into another task/session grants no authority. A submitted-message
record and its consumption are distinct: repeated delivery reconciles one
consumption, while different queued submissions remain individually accounted
for in arrival order. No last-message overwrite is permitted. Resume recovers
unconsumed submissions as well as unfinished work. Source collection must remain
limited to explicitly selected tasks, never all of the user's conversations.

The requirement registry is authored through one bounded change at a time:
record a requirement, attach one criterion, link an exact source range, inspect
uncovered source/criteria, and seal the independently reviewed registry. The
server derives excerpts and relation identity. A whole-request source range may
pass character accounting but cannot replace independent semantic review.
Review binds both source/mapping and criterion-content digests; editing either
invalidates the review. Initial extraction repairs may add omitted obligations;
after sealing, only a direct user change can reduce or replace the registry.

Plan construction likewise adds a node, binds one requirement/criterion, or
adds one dependency per operation, then validates and seals the assembled graph
atomically. Intermediate drafts grant no execution authority. Editing a draft
must not mutate active assignments or obligations. Worker dispatch selects ready
server-owned nodes; neither scope details nor the complete graph travel back in
the dispatch request. Server-issued selections are task- and audience-scoped.

The selected node's observed ownership fixes ordinary dispatch versus loss
reconciliation. A ready reconciliation node is claimed normally. A selected
unpublished active owner requires fresh host-confirmed quiescence before the
server may revoke and reconcile it. No caller-selected recovery mode may
reinterpret a ready node or bypass a live owner, exact scope or finite budget.

Evidence and narrative use separate small operations. One observation records
one already declared check against its assigned generation and a real execution
receipt. A Markdown report does not contain authoritative coverage flags. The
server derives completion from the complete required check set, current evidence,
independence and source coverage. A report may explain why work is incomplete
without being rejected merely for not declaring success. Terminal binding comes
from the consumed assignment, never a caller-selected report type.

Small operations still require transactional receipts, bounded drafts and
atomic sealing. A crash midway through construction resumes the exact draft;
it does not activate partial work or require resending an entire plan/report.
Do not implement this as a new facade over retained legacy public operations.

This document is the implementation contract for the Cortex orchestration
hardening work. It is authoritative for this change together with executable
source contracts and tests. The work is not complete until every invariant and
qualification level below is implemented and verified.

The objective is a hybrid architecture:

- the LLM chooses intent, decomposition, specialists, models, effort,
  parallelism among ready work, remediation, and user communication;
- the deterministic core stores revisions and the execution graph, computes
  readiness, binds workers, fixes each assignment's terminal result type, and
  rejects impossible transitions;
- workers consume one immutable assignment, perform only its scope, and emit
  exactly one result of the predeclared type;
- the user is involved only for a material plan decision, a genuine product or
  authority branch (normally in plan review, or before planning only when the
  alternatives cannot yet be validated), a directly authored change, and the
  mandatory post-result closure review.

The governing principle is:

> The model chooses intent; the system guarantees transition validity.

The backend remains an integrity gate, not an automatic scheduler. It may
return computed `ready` and `waiting` projections, but it never chooses which
ready node to dispatch.

The complete design preserves seven global invariants:

- **Safety:** no assignment, publication, approval, steering, recovery, or
  closure transition can commit against stale or unsatisfied authority.
- **Progress:** every nonterminal task has at least one observable condition:
  ready work, active work, a candidate requiring autonomous replanning, a
  genuine decision already permitted by section 8, or a bounded failed state
  with an admissible evidence/remediation route. Internal recoverable problems
  never create a user-facing dead end.
- **Monotonicity:** finalized evidence and graph lineage are append-only;
  revisions supersede authority without rewriting history.
- **Recoverability:** a new host connection can reconstruct the only admissible
  continuation from durable state without conversational memory.
- **Artifact consistency:** every implementation-dependent publication is bound
  to one observed artifact generation; evidence from an older generation never
  satisfies a newer one.
- **Convergence:** remediation has a finite, plan-declared evidence budget and
  must either make measurable progress, select a materially new validated
  strategy, or terminate honestly as exhausted.
- **Explainability:** every `waiting`, rejection, supersession, or incomplete
  closure projection includes a bounded machine-readable reason tied to the
  current graph, revision, capability, or decision relation.

## Non-goals

- Do not turn the backend into a semantic planner, role selector, or host
  scheduler. It may enforce the declared workflow state machine but never
  invent task meaning or choose the next worker.
- Do not encode MCP request shapes or parameter tutorials in skills, profiles,
  worker prompts, or live workloads. The advertised tool schema is the only
  call-shape authority.
- Do not require every conditional operation in an ordinary happy path.
- Do not weaken a contract merely to make a probabilistic live run pass.
- Do not change the semantic plugin version for this work. Refresh only the
  content-addressed cache suffix after the payload is frozen.
- Do not modify or reinstall the user's stable Cortex plugin.

## Codex host capability boundary

The architecture uses only host behavior already exposed to the plugin on Codex
CLI and Desktop, or static Cortex MCP/backend changes implemented inside this
repository.

Available Codex host mechanisms are:

- one fixed MCP operation registry with static input/output schemas; an
  explicit later catalogue read may return an audience projection, but the
  host is allowed to retain the initial catalogue;
- direct Cortex MCP request/response calls with structured success or error
  results;
- native `spawn_agent`, `list_agents`, `wait_agent`, `send_message`, and
  `interrupt_agent` coordination calls visible to the coordinator;
- `PreToolUse` and `PostToolUse` hooks that can validate directly surfaced tool
  calls, deny a call, record private correlation state, and add bounded context;
- `SessionStart`, `SessionEnd`, `SubagentStart`, `SubagentStop`, compact, and
  stop lifecycle observations;
- unchanged native spawn input carrying the exact server-rendered worker
  bootstrap;
- the supported zero-history native worker mode, which permits default Luna
  routing, explicit Terra/Sol overrides, and explicit effort without inheriting
  an unrelated parent transcript;
- first-worker-call binding through the exact worker-scoped `read_task`
  reference when Desktop has no trustworthy initialize-time child identity.

Packaged Cortex profiles are semantic assignment policy, not native Codex
agent types. The current native spawn contract carries the server-rendered
message and task name plus its supported model/effort routing fields; it has no
Cortex `role` or `profile_name` field. The selected profile and its immutable
instructions are therefore bound in Cortex state and delivered through the
unchanged bootstrap plus first `read_task`, never injected into an unsupported
host argument.

The design must not assume capabilities Codex does not expose:

- an MCP server cannot push an asynchronous message or cancellation into a
  running coordinator or worker;
- no hook can asynchronously terminate a running native subagent;
- `SubagentStart` continuation output cannot stop that subagent, and the host's
  `Interrupt` hook does not run for subagents;
- `SubagentStart` does not expose the spawn prompt or a complete authenticated
  assignment mapping;
- `SubagentStop` alone is not authoritative terminal-publication evidence;
- native spawn input must not be rewritten through `updatedInput`;
- the server cannot force a mid-turn catalogue refresh or depend on the host
  adopting a dynamically replaced per-worker catalogue;
- backend state cannot directly invoke Codex coordination tools.

Consequently, all server-to-model transitions are pull-based. The backend
stores authority and returns projections; the coordinator performs native
spawn, wait, message, and interruption actions. Hooks validate and correlate
host calls but never pretend to be a scheduler, cancellation transport, or
completion authority. Every new field described below is a static addition to
one of the existing twenty Cortex operations, not a dynamic tool or host API.

The implementation boundary is explicit:

| Required behavior | Implementable surface | Forbidden assumption |
| --- | --- | --- |
| Store and validate DAG revisions | Cortex backend and its existing publish/read operations | Codex schedules graph nodes |
| Compute ready/waiting nodes | Cortex backend; `read_scope` returns the projection | A hook infers semantic readiness |
| Start a worker | Coordinator calls native `spawn_agent` in supported zero-history mode with unchanged server-rendered input, default Luna or explicit Terra/Sol override, and explicit effort | MCP invokes a native agent, the worker inherits an unrelated transcript, or the spawn receives invented role/profile arguments |
| Bind a Desktop child | Exact first worker `read_task` plus private hook correlation | `SubagentStart` contains the spawn prompt |
| Wait or reconcile workers | Coordinator uses `wait_agent` and `list_agents` | Cortex polls private host state |
| Stop stale workers | Coordinator uses `interrupt_agent` after committed steering | Server or hook pushes cancellation |
| Reject a racing stale publication | Existing publish operation returns its static `superseded`/`published=false` variant | A pre-backend lifecycle interception always wins |
| Isolate writes in the shared checkout | Backend admits one artifact-mutating assignment at a time; independent read-only work may remain parallel | Native subagents automatically receive isolated worktrees |
| Bind evidence to project state | Workers compute the declared deterministic fingerprint; Cortex stores and compares generation tokens and lineage | Cortex or hooks can inspect the filesystem directly |
| Enforce resume ordering | Lifecycle hook marker plus `read_state` and, when required, `read_continuations` | Conversation memory proves recovery state |
| Select a reviewed alternative | Static plan-review relation and typed branch selector | Backend parses a branch from prose |
| Authorize closure | Existing clarification record plus backend graph/evidence gate | Worker completion implies permission to close |

The host claims in this section are bounded by the current official
[Codex subagent](https://learn.chatgpt.com/docs/agent-configuration/subagents),
[hook](https://learn.chatgpt.com/docs/hooks), and
[worktree](https://learn.chatgpt.com/docs/environments/git-worktrees) surfaces
and by the repository's observed
[`host-boundary-correlation-audit.md`](../architecture/host-boundary-correlation-audit.md)
and
[`native-spawn-updated-input-boundary.md`](../architecture/native-spawn-updated-input-boundary.md).
If a future implementation needs a host behavior absent from those surfaces,
this contract must be revised before code is written; it cannot be simulated by
prompt wording.

## Transaction and idempotency boundary

Every Cortex mutation is one SQLite transaction with one server-derived logical
slot, current authority revision, and normalized payload digest. The public
caller never invents receipt identity.

- The durable commit, timeline event, state transition, and command receipt are
  atomic.
- An identical retry after an ambiguous transport result returns the original
  committed projection with `replayed = true`; it never repeats the mutation.
- A different payload for the same logical slot returns a typed conflict before
  mutation.
- A response that explicitly reports a non-replayed success is final. The
  coordinator must not retry it, reopen the assignment, or spawn another worker.
- Read cursors and semantic selectors are bound to the task revision and
  projection digest that produced them. A later revision rejects them as stale
  rather than resolving them by name or position.
- Publication performs stale revision, assignment capability, expected terminal
  kind, artifact generation, payload, and uniqueness validation before consuming
  the one cross-kind terminal slot.

Native agent creation cannot share the database transaction. It is therefore a
small explicit saga: `open_assignment` commits one dispatch lease, the
coordinator performs exactly one unchanged native spawn for a non-replayed
success, and hooks correlate the host result. A definitive spawn failure follows
the loss-recovery path. An ambiguous spawn result is reconciled through the
protected task name and native agent projection before any replacement is
considered. Neither a hook failure nor a missing model-visible handoff can turn
an already committed assignment into permission to replay it.

This contract uses existing Cortex calls, SQLite, and current hook-visible host
operations. It requires no distributed transaction coordinator and makes no
claim that database rollback can undo a native host or filesystem side effect.

## 1. Declarative execution graph

### Governance binding and bootstrap admission

`open_task` is the first Cortex project-execution operation on a fresh
coordinator connection. The coordinator then records exactly one explicit
`assess_governance` selection before opening the first planning, discovery,
delivery, or evidence assignment. The backend rejects first-assignment
admission until both records exist in that order.

Governance does not choose a model, profile, plan, or graph. It binds the
current task revision to the required review and verification depth:

- `minimal` and `light` permit an informational no-question route when the
  finalized plan and graph-validation evidence are complete and no genuine
  branch, credential prerequisite, external authority, or explicit user review
  remains;
- `full`, materially high-risk work, and explicit user-requested review require
  plan review before any execution node can become ready;
- material risk-changing evidence may append one newer assessment before the
  next affected assignment; an identical normalized assessment/evidence digest
  cannot be used to churn assessments or reopen an approved plan.

The coordinator selects `full` only for material or high-risk work. Complexity
alone may justify Terra, deeper verification, or a larger graph, but it does
not justify another user approval. Thus governance depth cannot be used as a
back door that turns every nontrivial plan into a required review.

This is a static backend admission rule over the existing `open_task`,
`assess_governance`, and `open_assignment` operations. No hook performs the
assessment, and no host API is required to infer risk.

A finalized plan carries a typed DAG rather than only prose stages. The graph
is the executable interpretation of the current semantic contract; prose is a
human explanation and cannot grant readiness or ownership.

Every node has:

- a unique bounded semantic node key, stable only within its graph revision and
  safe to expose as a selector because it is not private ledger identity;
- a node kind: `planning`, `graph_validation`, `discovery`, `implementation`,
  `audit`, `remediation`, `verification`, or `documentation`;
- one responsibility boundary: `planning`, `delivery`, or `evidence`;
- one execution mode: `artifact_independent`, `read_only`, or `mutating`;
- an owner label without a preselected worker profile;
- zero or more uniquely named contribution keys it produces;
- zero or more exact current outcomes it verifies;
- for a mutating node, bounded declared mutation domains;
- zero or more predecessor node keys;
- typed capabilities it requires and produces;
- a completion policy and, for conditional nodes, a bounded activation rule;
- bounded work, acceptance, and verification descriptions.

The complete candidate graph or candidate family must fit the finite node,
edge, alternative, and aggregate-byte maxima advertised by the static
publication schema. Plan publication is one atomic call and does not assume an
unsupported streaming or hidden continuation channel. An over-limit candidate
is rejected before mutation; the planner must coarsen executable contributions
without omitting any contract outcome, acceptance condition, constraint, or
verification obligation.

Outcome relationships distinguish user-visible completion from executable
contributions:

- every contribution key has exactly one producer node in a graph revision;
- an executable outcome has one explicit completion expression over one or
  more contribution keys, using bounded `all_of` and validated conditional
  branches rather than implicit prose;
- several implementation nodes may therefore contribute independently to one
  outcome, while no contribution has ambiguous ownership;
- any number of audit or verification nodes may verify the same outcome or a
  named contribution;
- a producer cannot claim independent verification of its own contribution;
- decisions, constraints, and other non-execution contract items are marked
  explicitly and are not assigned fake contributions.

This permits frontend, backend, data, and integration workers to contribute to
one product outcome, followed by parallel architecture, security, database,
QA, and code-review verification, without duplicating the outcome or forcing
all implementation into one worker.

Dependencies are not bare ordering hints. A predecessor edge must satisfy at
least one required capability. For example, an implementation node may produce
`runnable_implementation` and `baseline_test_evidence`; an implementation-
dependent audit requires those capabilities. The backend checks that every
required capability has a reachable provider and that the provider is an
explicit predecessor. Node-kind rules additionally reject implementation-
dependent audit, verification, remediation, and documentation nodes with no
declared artifact dependency unless they are explicitly classified as
pre-implementation discovery.

Plan publication creates a candidate graph; it does not immediately authorize
delivery. The backend first performs deterministic structural validation:

- node keys are unique and every referenced dependency exists;
- self-dependencies and cycles are rejected;
- contribution ownership is complete and unique, every outcome completion
  expression is satisfiable, and verifier relationships may be many-to-many;
- every required capability has an unambiguous reachable producer;
- node kinds, activation rules, completion policies, and publication kinds are
  mutually compatible;
- execution modes and mutation domains are present where required and do not
  claim unsupported physical isolation;
- every replanning, additional-evidence, remediation, regression, and
  reconciliation expansion has a finite validated budget and an observable
  progress condition;
- planning-publication completion is valid only for the planning node;
- an ordinary graph is bound to the exact task revision and plan digest; each
  decision-bearing alternative is bound to the same base revision, its complete
  proposed semantic-delta digest, and the candidate-family digest.

Structural validity cannot prove that a planner understood the project or
declared every semantically necessary dependency. Therefore every nontrivial
candidate graph must pass one independent `graph_validation` assignment before
activation. Its immutable evidence contains the complete candidate graph,
current semantic contract, finalized discovery evidence, and source-to-contract
coverage projection. The verifier checks:

- every source requirement is represented as an outcome, verification
  obligation, decision, or constraint;
- contribution-owner/verifier relationships and capability declarations match
  the proposed work rather than merely satisfying schema shape;
- implementation-dependent nodes cannot become ready without the artifacts
  they intend to inspect;
- conditional remediation policies cover every audit capable of reporting a
  defect;
- node responsibilities, mutation boundaries, and verification independence
  are coherent.

The verifier publishes a normal result. Complete evidence makes the exact
candidate digest activation-eligible; partial, blocked, or failed evidence
leaves it inactive and routes autonomous replanning. A replacement planner
publishes a new candidate digest rather than mutating the rejected candidate.
A plan with no unresolved user decision activates before informational or
required review. A decision-bearing plan is handled as the validated candidate
family defined in section 8 and activates only after branch selection. Delivery
admission is impossible against an inactive graph. Activation alone does not
bypass approval: when governance requires review, execution nodes remain
`waiting` on the graph's approval relation until the current approval is
recorded.

The graph verifier is independent of the planner: it receives no planner
identity or conversational rationale beyond finalized plan evidence, cannot
edit the candidate, and cannot activate it by assertion. The backend activates
only when structural validation and complete graph-validation coverage agree.
A generated single-node minimal graph is the sole exception because its
topology and contract mapping are constructed entirely by the backend.

The activated baseline graph is immutable. Steering creates a new task
revision and invalidates it. Evidence-backed remediation extends execution only
through the prevalidated conditional templates in section 3; it never rewrites
the baseline graph.

## 2. Readiness projection and assignment admission

Each current node has exactly one lifecycle state:

- `waiting`: at least one required predecessor, capability, activation fact,
  or approval relation is unsatisfied;
- `ready`: all prerequisites are satisfied and no conflicting owner exists;
- `active`: exactly one live assignment owns the node;
- `complete`: its terminal evidence satisfies its completion policy;
- `partial`: terminal evidence covers only part of the node contract;
- `failed` or `blocked`: terminal evidence does not satisfy dependants;
- `resolved`: an originally failed node has every blocking finding covered by
  a completed current remediation/regression chain;
- `exhausted`: finite remediation/strategy budgets ended without satisfying
  the node; this is terminal but never satisfies dependants;
- `skipped`: a conditional node was deterministically not activated;
- `stale`: its graph or task revision is no longer current.

Predecessor satisfaction is explicit, not inferred from prose:

| Predecessor disposition | Satisfies a required dependency |
| --- | --- |
| `complete` with every required fact `executed` | Yes |
| `resolved` with restored required capabilities | Yes |
| `complete` with permitted non-applicable `not_run` | Only when allowed by the edge |
| conditional `skipped` | Only for an optional branch |
| `active`, `partial`, `blocked`, `failed`, `exhausted`, or `stale` | No |

A node becomes `complete` only when every node-scoped produced contribution or
verified outcome has complete coverage, every mandatory verification
fact has an executed success state, every required artifact-generation binding
matches, and the expected terminal publication kind was used. An outcome is
complete only when its completion expression is true. A result containing a
failed fact cannot be complete. A `not_run` fact is acceptable only for a check
that the graph marked optional or non-applicable before assignment; it cannot
be used to conceal an omitted mandatory check.

`read_scope` exposes a neutral current-revision projection of semantic node
keys, lifecycle state, produced contributions, verified outcomes, artifact
generation, and bounded unmet predecessor/capability facts. It does not
recommend a profile, model, effort, or scheduling decision.

Semantic readiness is separate from native host capacity. A node remains
`ready` while it waits for a free subagent slot; capacity pressure never
rewrites it to `waiting`, weakens its dependencies, or justifies combining
unrelated scopes. The coordinator obtains the current native projection through
`list_agents`, retains the agent references returned by successful spawns, and
dispatches only as many compatible ready nodes as the host can currently run.
The coordinator prioritizes nodes on the blocking critical path, then
higher-risk verification, then older ready nodes. This is explicit coordinator
policy, not a claim that the probabilistic host or backend is a deterministic
scheduler. Lack of capacity causes bounded waiting through `wait_agent`, not a
user question or a duplicate assignment. The backend remains the readiness and
ownership authority; Codex remains the capacity and dispatch authority.

Assignments select exact semantic node keys from the immediately preceding
current scope projection. Outcome names are coverage subjects, not assignment
identities, because several independent nodes may verify the same outcome. The
coordinator selects node keys, profile, model, and effort; it does not restate
node goal, scope, acceptance, verification, or evidence policy in free-form
assignment text. The server derives the exact predecessor publications and
contract evidence required by those nodes, then renders the complete immutable
worker assignment. This removes drift-prone duplicate representations of both
assignment ownership and evidence selection.

Inside one mutation transaction, `open_assignment` rejects the request before
minting a worker when:

- any selected node is not `ready`;
- selected nodes have incompatible mutation ownership or terminal kinds;
- selected nodes have different target artifact generations or incompatible
  execution modes;
- the graph, scope projection, plan relation, or task revision is stale;
- a selected node already has a conflicting owner.

The same transaction changes every selected node from `ready` to `active` and
binds one assignment capability. Concurrent attempts against an overlapping
node cannot both succeed. Publication atomically consumes that capability,
records the one terminal result, recomputes generated remediation and dependant
readiness, and only then exposes the new projection. Parallelism remains
unrestricted only for mutually compatible ready nodes under the artifact
generation and mutation-barrier rules below.

### Artifact generations and mutation barrier

Cortex cannot inspect or isolate the project filesystem. It therefore models
observable project state without pretending to own it. Each implementation
wave produces an append-only artifact generation containing:

- a generation key derived by the backend from the task revision, producing
  publications, and their order;
- one declared fingerprint method selected by the plan from supported worker
  procedures;
- the worker-observed start and end fingerprints;
- a deterministic changed-path commitment containing count, digest, bounded
  human-readable samples, mutation-domain conformance, and produced
  contribution keys;
- predecessor generation lineage.

For a Git checkout, the supported procedure hashes the resolved HEAD plus the
complete staged, unstaged, and untracked-content state relevant to the declared
project boundary. For a non-Git project, it hashes a deterministic bounded
manifest of the declared artifact paths. The backend stores and compares these
opaque values; it does not claim that a digest is host attestation or read files
itself. The assignment tells the worker which procedure to execute, and an
independent verifier repeats it.

Worker-generated manifests live in owner-private system temporary storage,
outside both the project and the MCP-owned ledger tree. The namespace isolates
the OS user, Codex home and project. This keeps the procedure executable in
ordinary workspace-write sandboxing without widening permissions. Only hash
metadata is stored; scratch loss means comparison evidence is unavailable and
requires bounded recovery, not an inferred matching fingerprint.

The shared-checkout admission policy is conservative and implementable on
current Codex:

- at most one `mutating` assignment may be active for one canonical project
  root;
- no artifact-dependent `read_only` assignment may overlap an active mutator;
- once a generation is sealed, any number of independent read-only audits may
  run in parallel against it;
- while such audits are active, no mutator may start against that root;
- `artifact_independent` research may run in parallel because it declares no
  dependency on mutable project state.

This is a ledger admission barrier, not a filesystem lock. Native worktree
isolation is neither required nor claimed. External or non-Cortex changes
remain possible and are detected by the fingerprint protocol at the next
artifact-dependent publication boundary.

The guarantee is therefore boundary-consistent, not continuous filesystem
attestation. Closure certifies the latest project generation observed by the
final verification and documentation publications. Current Codex/host APIs do
not provide an atomic filesystem seal spanning the last worker observation,
user review, and `close_task`; an external edit after the last fingerprint and
before closure cannot be detected retrospectively by Cortex. Qualification
must run in a controlled project with no external writer during that interval,
and the result must state this boundary instead of claiming immunity to
out-of-band mutation.

An artifact-dependent assignment also forbids detached or unowned background
processes that can mutate the project after the worker's final fingerprint or
terminal publication. A bounded child process may be used only when the worker
waits for it and verifies termination before publishing. Hooks can observe
supported tool calls but cannot prove that every operating-system descendant
has stopped, so this is an assignment constraint reinforced by the end
fingerprint and controlled qualification environment, not a fictional host
sandbox guarantee.

Every artifact-dependent worker records the target generation and observes its
fingerprint before work. A read-only worker observes it again immediately before
publication, and the first publication call carries both observations. The
backend publishes read-only evidence only when both match the sealed target
generation. A mutating worker must begin from that same target fingerprint; its
end fingerprint and changed-path commitment instead seal the successor
generation. The complete changed set is hashed by the worker rather than copied
into an unbounded MCP payload; an independent verifier recomputes the commitment
before it can satisfy final verification. A start mismatch, a read-only end
mismatch, or reported mutation-domain nonconformance returns the static
non-publication variant
`state = snapshot_conflict`, `published = false`, consumes no terminal slot,
revokes that assignment's publication capability, raises
`reconciliation_required`, and instructs the worker to stop. Reconciliation
does not become ready until native evidence confirms that worker is quiescent.
The reconciliation node then observes the current project state and creates a
new artifact generation without claiming a mutation. Evidence bound to the
older generation remains historical and cannot satisfy current dependants.

A successful mutating publication always seals a new generation. It
invalidates readiness and completion of every downstream audit, verification,
or documentation node bound to an older generation, then recomputes the DAG.
This rule applies to every mutating publication kind, including a documentation
worker that edits project documentation. Such documentation mutation must
precede final verification. A documentation-impact assessment that changes no
files is read-only and may run beside other read-only checks against the same
sealed generation.
Independent read-only publications are valid only when their start fingerprint,
end fingerprint, and target generation agree. Closure requires the final
verification and documentation-impact evidence to share the latest sealed
generation.

A worker loss before publication revokes the abandoned assignment without
creating terminal evidence. Loss is established only when a supported native
coordination result identifies the exact agent as terminal, aborted, or
unrecoverable and reconciliation finds no finalized publication for its bound
assignment. `SubagentStop` may corroborate that result but is never sufficient
by itself; a timeout, silence, or coordinator inference is not loss evidence.
After resume, the combination of the terminal `read_continuations` projection,
a complete successful `list_agents` projection in which the protected task name
is absent, and no finalized publication is sufficient native loss evidence.
An unavailable, failed, truncated, or ambiguous agent projection is not.
The node returns to `ready` with a bounded `recovery_required` reason, and only
a lineage-linked replacement assignment is admissible. Recoverable loss is
handled autonomously and never becomes a user question.

Discovery, planning, and candidate graph validation run in a system-generated
typed bootstrap graph. Discovery nodes are read-only, publish results, and
cannot own delivery outcomes. When the coordinator decides that a distinct
pre-plan question is necessary, assignment opening atomically appends and claims
one bounded discovery node in the bootstrap graph. That request defines the
node's single evidence question once; the server stores it and renders the
worker scope, so no separate assignment-text copy exists. Bootstrap admission
enforces read-only responsibility, non-overlap with active discovery nodes, and
no delivery or user-decision authority.

The planning node is generated from the complete current contract and finalized
bootstrap evidence when the coordinator selects planning. Its plan publication
carries the candidate execution graph; the candidate does not contain or
complete its own planning node. Publishing a structurally valid candidate
deterministically creates its candidate-bound `graph_validation` bootstrap
node. Rejected candidates create new append-only planning generations in the
same bootstrap graph.

When the coordinator selects the minimal no-plan route, the backend generates
one execution node covering the complete contract and exposes it through the
current scope projection. A task requiring dependent audits, multiple mutation
owners, or unresolved branches is ineligible for this route. Thus minimal work
never enters a second, graph-free admission system.

Free native-agent slots never make a waiting node ready. The canonical order
for implementation work is:

```text
research
  -> implementation and baseline checks
  -> ready dependent audits, parallel where independent
  -> fixes for confirmed defects
  -> documentation updates when required
  -> independent final verification and read-only documentation assessment
  -> closure review
```

An architecture, database, security, code, QA, build, accessibility, or other
implementation-dependent audit opened before acceptable implementation
evidence is a rejected transition, not a worker that later reports `partial`.

## 3. Deterministic remediation expansion

Incomplete implementation and audit findings can reveal work that could not be
described before execution. The plan therefore defines conditional completion
and remediation templates rather than inventing speculative fix nodes or
allowing arbitrary graph mutation.

Every implementation, audit, remediation, or verification node that can return
remediable `partial`, `failed`, or `blocked` evidence declares a validated
template with:

- the outcomes and capabilities a fix is allowed to modify;
- the finding classifications that activate it;
- the audit evidence it must consume;
- a remediation node kind and mutation boundary;
- an independent regression-verification node;
- the capabilities restored only after regression succeeds;
- the downstream nodes that remain waiting while the chain is active.

Terminal coverage classifies each incomplete or nonconforming fact as exactly
one of:

- `defect_within_contract`: the implementation violates an already authorized
  outcome, acceptance condition, or constraint;
- `contract_change_required`: correction would change product behavior or
  semantic scope;
- `authority_required`: correction needs credentials, external permission,
  destructive action, deployment authority, or another unavailable grant;
- `risk_change_required`: correction materially changes the assessed risk;
- `inconclusive`: evidence is insufficient to classify the result.

The model owns this semantic classification; the backend never pretends to
infer product meaning. Transition validity is guaranteed relative to the
worker's structured attestation and the independently validated plan policy.
For security-sensitive or otherwise high-risk audits, the graph requires a
second independent classification-verification node before remediation or
steering routing becomes admissible.

The two classifications must agree. Disagreement produces `inconclusive`
evidence, keeps downstream work waiting, and routes another independent
evidence assignment within the graph's finite evidence budget; the backend
never resolves semantic disagreement by vote or by choosing the most convenient
transition. Exhausting that budget produces terminal `exhausted` evidence
rather than another worker.

`defect_within_contract` deterministically instantiates one append-only
remediation/regression chain from the prevalidated template. Instantiation is
idempotent by source finding and graph revision, stores private evidence
lineage, and introduces no public operation. `read_scope` exposes the generated
semantic nodes when they become ready; the coordinator still chooses profiles,
models, effort, and dispatch order. Repeated regression failure creates a new
evidence-linked generation without editing prior nodes or reports.

Remediation must make observable progress. A new generation must cite the
immediately preceding failed regression. The backend rejects an identical
mutation replay and preserves every generation; the coordinator, not the
backend, determines a materially different repair approach from current
evidence. An unchanged assignment may not be reopened merely to simulate
progress.

Every conditional remediation template declares finite generation and
strategy budgets during plan validation. The exact values depend on the risk
and verification cost of that node, but zero, missing, or unbounded budgets are
invalid. The backend maintains a progress fingerprint over the task revision,
candidate graph, artifact generation, unresolved finding fingerprints, failed
verification facts, and selected strategy generation.

A remediation attempt is progressive only when its publication produces a new
artifact generation and the following independent regression either resolves
at least one prior finding or adds new causal evidence that justifies the next
prevalidated strategy. Merely changing prose, profile, model, effort, or task
name is not progress. Repeating the same progress fingerprint is rejected
without consuming another generation.

When a template's generation budget is exhausted, one bounded diagnostic
evidence node may classify whether a materially different already-authorized
strategy exists. If so, a new independently validated strategy generation may
use the remaining finite strategy budget. When no authorized strategy remains,
or that budget is exhausted, the affected node becomes terminal `exhausted`.
It cannot satisfy dependants or trigger another autonomous retry. The
coordinator reports the incomplete evidence without closing the task or changing
its obligations. A materially different safe in-contract strategy requires
independent validation, not another user authorization. An exhausted strategy
does not authorize closure or erase the unfinished requirement.

The same bounded-convergence rule applies to rejected candidate plans,
additional classification evidence, snapshot reconciliation, and recovery.
Every append-only generation must change its declared progress fingerprint;
every route has a finite budget; exhaustion becomes explicit terminal evidence.

The other classifications never activate remediation silently:

- `contract_change_required` creates a decision-bearing candidate family; the
  selected plan-review branch applies the required semantic delta without a
  fabricated steering response;
- `risk_change_required` requires a newer governance assessment and candidate
  graph, plus plan review when the resulting governance requires it; it does
  not alter the semantic contract unless a real user decision selects such a
  change;
- `authority_required` is satisfied only by observable native host/user
  authorization or provisioned credentials. The need and consequence are
  surfaced in a decision-ready plan, but approving that plan alone is not
  authority evidence;
- `inconclusive` routes additional evidence or replacement work and cannot
  satisfy downstream dependencies.

A recoverable implementation failure or partial result uses the same bounded
template mechanism even when no audit preceded it. A blocked fact caused by an
internal tool, environment, or missing generated artifact is classified and
routed to recovery evidence or remediation; it cannot be converted into an
unnecessary user decision.

An activated remediation chain does not invalidate plan approval because its
policy was part of the independently validated graph. Its complete lineage is
closure evidence. Steering invalidates the baseline graph and every nonterminal
generated chain. Arbitrary coordinator-authored graph amendments are forbidden,
so remediation cannot smuggle behavior or authority past plan validation.

Successful independent regression changes the current projection of each fully
covered source node from `failed` to `resolved` and restores only the
capabilities named by the template. It does not rewrite the original failed
report. A source node with any unresolved, inconclusive, stale, or newly failed
finding remains blocking.

## 4. Typed terminal publication

Every graph node determines one terminal publication kind, and every assignment
stores that kind immutably when it is created:

- `planning` node -> plan publication;
- `documentation` node -> documentation publication;
- every other node kind -> result publication.

The expected kind is included in the immutable worker assignment view. The
backend rejects every other publication operation before a terminal slot is
consumed. A worker never infers its publication kind from whether files were
changed; read-only exploration and audits still publish results.

The native lifecycle binding and first successful assignment consumption are
both required before publication. Assignment capability is bound to one worker
session and cannot be transferred, guessed, reconstructed, or consumed by a
coordinator or sibling worker.

Exactly one terminal publication is allowed across all publication operations
for one assignment. Per-kind uniqueness is insufficient.

The publication kind is derived from node purpose, never from profile name.
A technical writer may perform a result-producing review, and another suitable
profile may own documentation when the coordinator has evidence for that
choice.

Human-readable plan and report files are derived projections, never authority.
After the database commit, the server renders the exact finalized record to an
owner-only file, reads it back, verifies its content digest, and only then
returns a complete server-generated Markdown link. The coordinator copies that
link byte-for-byte and never reconstructs a path. Plan review cannot open until
the current finalized plan projection has a verified link; closure review uses
the same rule for every result presented to the user.

Projection failure after a durable report commit does not roll back or duplicate
the report. An identical publication retry reconciles the existing receipt and
may repair only the derived projection before returning the same report
relation. A changed retry conflicts. A missing, unreadable, digest-mismatched,
or stale human-view file makes review unavailable and routes deterministic
projection repair; it is never represented as a valid link or silently replaced
with a guessed path.

## 5. Canonical verification evidence

Outcome coverage is the single public source of observed verification facts.
Each coverage item contains exactly one disposition for its assigned produced
contribution or verified outcome and a non-empty list of structured facts:

```text
check_key: exact assigned check key (not its description)
state: executed | not_run | failed
summary: bounded observable evidence
```

A failed or nonconforming audit fact also carries one remediation
classification from section 3. The server derives a stable private finding
fingerprint from the assignment, contribution or outcome, and fact; callers never create or
pass finding identity. Classification is evidence, not proof of semantic truth,
and therefore remains subject to the graph's independent verification policy.

There is no second caller-authored top-level verification-facts array in result
or documentation publications. Internal summaries and indexes are derived from
outcome coverage without changing state or wording.

Artifact generation keys, fingerprint digests, changed-path commitments, and
lineage are canonical integrity metadata rather than a second semantic
verification narrative. They answer which project state was checked, while
outcome coverage answers what was observed. Any human-readable fingerprint
check summary is derived into the applicable coverage fact instead of being
authored twice.

Every terminal result explicitly accounts for artifact observations. Read-only
and mutating assignments require their observed integrity metadata, including
an unchanged baseline. Artifact-independent assignments explicitly declare no
observations with null. Omission is invalid, not an implicit independent mode;
the server checks the immutable assignment mode before accepting publication.

Plan-node verification descriptions remain expectations, not claims that the
checks already ran. A plan publication therefore carries planned coverage with
expected checks but no observed fact state; it does not fabricate `not_run`
evidence. Observed outcome coverage exists only in result and documentation
publications. The backend rejects contradictory combinations according to the
readiness matrix in section 2.

## 6. Revision and steering atomicity

A direct user-authored semantic change is recorded immediately. It is never
queued behind an older-revision worker.

One steering transaction:

1. validates exact current outcome retirement and complete additions;
2. increments the effective revision;
3. invalidates the previous plan graph;
4. marks every nonterminal older-revision assignment stale;
5. invalidates their continuation and pending decision authority;
6. places the canonical project root behind a `reconciliation_required`
   mutation barrier;
7. returns a bounded effect summary containing the new revision, counts of
   invalidated relations, and the stale assignments' semantic node keys and
   protected native task names needed for host reconciliation.

Finalized evidence for unchanged outcome identities remains auditable. It is
not silently treated as current execution authority when the new graph requires
fresh work.

The MCP server cannot stop a native Codex agent. After the steering call
commits, the coordinator matches each returned protected task name to the
agent reference retained from the corresponding successful `spawn_agent` call
and invokes the supported `interrupt_agent` operation for every still-active
older-revision worker. The steering mutation is never delayed while waiting
for interruption. Hooks may validate and observe the coordinator's
interruption calls, but they do not synthesize cancellation or terminate agents
themselves.

After resume or compaction, the coordinator first completes section 7 recovery,
then reconciles the continuation task names and semantic node keys with the
current `list_agents` projection. It interrupts each matching still-active
agent. A worker already absent from that projection needs no fabricated
interruption receipt; durable stale assignment state remains authoritative.

Revoking publication does not undo filesystem changes a stale worker may have
made before interruption. Therefore no planning, delivery, audit,
documentation, or verification assignment for the new revision becomes ready
until all protected stale task names are confirmed absent or terminal through
supported native coordination evidence. Hooks record only the bounded
spawn/interrupt/list/wait correlations already visible to Codex; they do not
infer project state.

Once the stale workers are quiescent, the bootstrap graph exposes exactly one
read-only reconciliation assignment. It compares the last sealed artifact
fingerprint with the current project state, records observed changed paths, and
seals the new revision's baseline artifact generation without claiming that
the changes were reverted or authored by a particular worker. The revised
planner receives this evidence and decides whether the observed state is still
usable, needs in-contract remediation, or creates a genuine decision branch.
Only successful reconciliation releases the mutation barrier.

Interruption and a worker publication can race. Every existing publication
operation therefore performs the stale-revision check inside its transaction,
before validating or consuming the terminal slot. If the assignment is stale,
that called operation returns a schema-valid structured result with
`state = superseded` and `published = false`. This result:

- creates no report, evidence, completion, or terminal disposition;
- consumes no publication slot and cannot satisfy a graph node;
- is a successful request/response exchange but is not a successful
  publication;
- is neither an error nor a retry authorization;
- instructs the worker to stop without another Cortex call or further project
  work.

The static output schema of each publication operation includes this
non-publication result variant. No dynamic catalogue refresh, asynchronous MCP
push, hook denial, invented lifecycle event, or backend invocation of a host
coordination tool is required.

## 7. Deterministic recovery ordering

On a fresh coordinator connection that resumes an existing task:

1. `read_state` is the first task-scoped operation;
2. state returns scalar recovery facts and a neutral `admissible_operations`
   projection computed from the connection and task state;
3. when unfinished delegated work exists, the only task-progress read admitted
   next is `read_continuations`;
4. the connection gate rejects scope, outcome, evidence, timeline, steering,
   plan, assignment, or closure mutations until the bounded continuation view
   is consumed;
5. after the terminal continuation page, ordinary current-state routing resumes.

On host resume or compaction, the existing lifecycle hooks record a private
recovery-required marker from the session metadata Codex supplies. Ordinary
startup of a new task retains the required `open_task` first-call path.
`PreToolUse` can reject an out-of-order task mutation, and `PostToolUse` can
observe successful completion of the required read; neither hook invents task
state or invokes Cortex on the model's behalf. The first successful
coordinator `read_state` binds the resumed task to that connection and lets the
backend compute whether `read_continuations` is required. The gate applies only
to coordinator recovery and never to a worker's first `read_task`. It does not
force continuation reads after every ordinary same-connection state inspection.

`read_timeline` is never a continuation lookup. A queued direct user change is
recorded immediately after the required recovery view, before waiting for or
dispatching older-revision work.

For every continuation reported active after recovery, the coordinator obtains
one complete `list_agents` projection. A matching live task name is retained and
waited on; an absent task name follows the loss rule in section 2. Transient host
failures use bounded retries of the native read without mutating Cortex. If a
complete projection cannot be obtained, the task terminates truthfully as
`not_ready` with host reconciliation unavailable; it does not guess loss,
duplicate the assignment, or ask an unrelated product question.

## 8. User decision boundaries

There are exactly two semantic decision routes, selected by whether complete
alternatives can be planned safely before the answer.

When every safe alternative can be specified and validated, an unresolved
product, authority, or material-risk branch is represented as a decision-bearing
candidate family, not asked as an execution-time clarification. The planner
publishes the alternatives, each containing a complete semantic delta and
candidate graph. Structural and independent graph validation cover every
alternative before the review is shown.

The plan-review packet presents the alternatives and their consequences. Its
server-issued relation exposes bounded semantic branch keys. For a pending
candidate family, the static `record_plan_review` contract requires one of
those keys alongside the ordinary approve decision; the backend never infers a
branch by parsing free-form user text. One direct user response can therefore
select a branch and approve it. Recording that response atomically applies the
selected semantic delta, advances the task revision, binds the already
validated selected graph to that revision, and records its approval. No
intermediate graph becomes executable, no second confirmation is asked, and
unselected alternatives receive no authority. A response requesting plan
revision creates a new planning generation without changing the semantic
contract unless the response itself states a concrete change.

When a missing user choice determines the semantic contract and at least one
alternative cannot be responsibly planned or validated before that answer, the
coordinator opens one pre-plan steering hold and presents the concrete branch
and consequences. `record_steering` applies the direct answer atomically as the
new contract revision. Planning then continues from that revision. This route
is valid only for a genuine scope, behavior, authority, or verification choice;
a no-op delta is rejected. It does not trigger a second plan review unless the
resulting plan independently meets the material-risk, authority, credential, or
explicit-review criteria. Thus `open_steering` has a natural state-machine
precondition and is never called merely for tool coverage.

- Minimal, complete, risk-free plans may proceed informationally.
- Full/material-high-risk plans, explicit user-requested plan review, unresolved
  product branches, or external key/ENV prerequisites require one decision-ready
  plan review.
- Once the current plan is approved, all bounded work continues to completion
  without additional Cortex plan or clarification questions.
- Direct user changes are steering decisions and do not require duplicate
  confirmation.
- Direct steering authorizes the stated change. Its revised graph proceeds
  informationally without another plan question unless the change introduces a
  new material/high-risk branch, new external authority, a key/ENV prerequisite,
  or the user explicitly requests renewed review.
- Internal failures, missing files, stale workers, recoverable environment
  issues, and ordinary rework are resolved autonomously when possible.
- Every closure attempt requires the current verified result to be presented,
  followed by a fresh revise-or-close review. `close_task` is impossible before
  the explicit current `close` choice is recorded.

Plan approval is orchestration evidence, not a replacement for Codex's native
permission system. It cannot grant filesystem, shell, network, destructive,
deployment, credential, or external-service authority that the host has not
already granted. A predictable host approval or credential prerequisite is
disclosed in the decision-ready plan; an approval prompt later emitted by
Codex itself is not a second Cortex plan review and must not be suppressed or
forged.

The explicit close choice is necessary but not sufficient. The backend also
rejects closure while any required current node is waiting, ready, active,
partial, blocked, stale, failed, or exhausted without a complete resolving
chain; while any activated remediation/regression work is incomplete; while
documentation impact is unresolved; while final verification and documentation
refer to different artifact generations; or while the result presented for
review predates the latest graph evidence. Optional branches must be
deterministically `skipped`, not silently absent. An incomplete task remains open;
there is no incomplete-verdict closure route. Even a direct close answer cannot
waive mandatory requirements or current-generation verification.

## 9. Model and effort policy

- Luna is the orchestration default for the large majority of assignments and
  may use effort through `max`. Cortex records Luna as its selected model, but
  native spawn omits the model override and uses the host's configured default
  subagent model. The supported environment must configure that default as
  Luna; qualification verifies this prerequisite. Do not pass Luna explicitly
  in the native spawn call.
- Terra is reserved for genuinely complex planning, architecture, or similarly
  difficult systems reasoning and may use effort through `max`; its native
  model override is explicit.
- Sol is reserved for rare, materially risky security-sensitive work; its
  native model override is explicit.
- `ultra` is never selected or advertised as an orchestration route.

Effort is explicit on every native worker spawn. Terra and Sol use explicit
model overrides; Luna deliberately uses the configured default route, not a
legacy fallback. Cortex workers use
the host's supported zero-history mode because the unchanged server-rendered
bootstrap and first `read_task` already carry their complete authority and
evidence. This prevents inherited conversation from becoming a second scope
source and permits the selected Luna, Terra, or Sol route to take effect.
Model selection remains an LLM decision within the server-enforced bounds.

## 10. Qualification ladder

The payload is frozen before live qualification. A failure returns to the
smallest affected level; the full matrix is not used as a debugging loop.

### Level A: public contracts and all 20 operations

Exercise every public operation with its natural positive precondition and
with representative invalid transitions. Conditional operations use dedicated
scenarios: point replacement, recovery, chronology, plan review, and closure
review. Acceptance is zero unexpected errors and exact expected error codes for
negative calls. Contract tests also prove that assignment scope/evidence are
server-rendered from node selection, plan expectations are not represented as
observed facts, result/documentation verification has one canonical source, and
the static publication schemas include both `superseded` and
`snapshot_conflict` non-publication variants.

The positive matrix is fixed so coverage cannot be manufactured with no-op or
out-of-state calls:

| Operation | Natural positive scenario | Required observation |
| --- | --- | --- |
| `open_task` | Fresh coordinator begins a real bounded project task | One current contract and task relation are committed |
| `assess_governance` | The opened task receives its initial risk assessment | Assessment precedes every assignment |
| `read_scope` | Coordinator needs current ready/waiting node projection before dispatch | Returned node keys belong to the current revision |
| `open_assignment` | Compatible nodes are ready and native capacity is available | One dispatch lease owns exactly those nodes |
| `read_task` | Newly spawned worker consumes the exact server-rendered bootstrap | This is that worker's first Cortex call and binds its capability |
| `publish_plan` | Planning worker has completed a candidate graph | Candidate is stored once and routed to validation and review policy |
| `publish_result` | Other non-documentation worker finishes its assigned node | The predeclared result slot is consumed on the first call |
| `publish_documentation` | Documentation node finishes its assessment or authorized update | Documentation impact and coverage are stored once |
| `read_evidence` | Coordinator prepares a review, result presentation, or evidence audit | Only the selected finalized evidence projection is returned |
| `open_plan_review` | Validated plan is materially high-risk or review was explicitly requested | One decision is bound to the verified current plan projection |
| `record_plan_review` | User directly decides the pending current plan review | Exactly that decision and any validated branch are consumed atomically |
| `open_steering` | A genuine pre-plan choice is needed to construct a valid contract | One decision is bound before the question is rendered |
| `record_steering` | User answers that branch or directly authors a semantic change | A non-empty contract delta creates one new revision |
| `read_outcome` | A point replacement must preserve untouched fields of one current outcome | The complete outcome is read before atomic replacement |
| `read_state` | Coordinator resumes or reconciles after a concrete lifecycle/change event | Current scalar recovery and closure facts are returned |
| `read_continuations` | Resumed state reports unfinished delegated work | Continuations are consumed before other progress operations |
| `read_timeline` | Chronology or audit is explicitly needed | History is read without substituting for current state |
| `open_clarification` | Verified result was presented and mandatory closure review begins | Fresh revise/close authority is bound to that result |
| `record_clarification` | User directly chooses revise or close | The exact current choice is recorded without inference |
| `close_task` | Current close choice and every graph/evidence/generation gate are satisfied | One truthful ready or not-ready terminal state is committed |

Each row has its own focused positive test and relevant negative transitions.
The full live scenario must reach the same states through ordinary work, but it
cannot replace these contract tests or synthesize their preconditions merely
to increment a coverage counter.

For every Cortex command operation, lose the first response after commit and prove an
identical retry reconciles the original receipt while changed input conflicts.
For assignment publication, prove reconciliation never produces a second native
spawn or terminal report.

### Level B: all 22 packaged profiles

For every profile, independently verify:

```text
select ready semantic node -> open assignment -> read_task first
-> exact immutable node scope
-> predeclared terminal publication kind -> first-call success -> stop
```

No corrected publication, substituted profile, second terminal publication, or
unexpected coordinator authority is accepted.

### Level C: DAG integrity

Use a focused multi-contributor implementation/audit graph. Prove unique
contribution ownership, satisfiable outcome completion expressions,
many-to-many independent verification, capability reachability, cycle
rejection, and the complete predecessor-disposition matrix. Frontend and
backend contributions must combine into one outcome without duplicate outcome
identity. Dependent audits must be reported waiting and rejected before the
outcome's required contributions and artifact generation exist, become ready
only afterward, and then be independently dispatchable in parallel. Omit a
required capability edge or contribution deliberately and prove graph
publication fails. A partial audit caused only by a missing predecessor fails
the level.

Race two assignment openings against one ready node and prove exactly one can
commit. Publish one terminal result while independent nodes finish in parallel
and prove dependant readiness appears only after the entire publication
transaction commits.

Race two mutating nodes for the same project and prove the mutation barrier
admits exactly one while retaining eligible artifact-independent work. After
the mutator seals a generation, run multiple read-only audits in parallel and
prove no mutator becomes admissible until they terminate. Inject an external
artifact change between an audit's two observations and prove its first
publication returns `snapshot_conflict`, creates no report, consumes no slot,
and causes reconciliation to create a successor generation. Prove every audit,
verification, and documentation result bound to the old generation becomes
historical and cannot satisfy closure.

Prove that structurally valid but semantically incomplete candidate graphs do
not activate: the independent graph validator must detect missing contract
coverage, false independence, and omitted artifact dependencies. Delivery must
remain inadmissible until one exact candidate digest passes both structural and
independent validation.

Then report one evidence-backed in-contract defect and prove deterministic,
idempotent remediation-template expansion, downstream re-blocking, independent
regression verification, the source node's `resolved` projection, and return to
the baseline graph. Prove that contract,
authority, risk-change, and inconclusive classifications cannot activate that
template. Repeat a regression failure and prove a new append-only generation is
created without altering prior evidence. Exhaust the finite generation and
strategy budgets and prove the node becomes terminal `exhausted`, no autonomous
identical retry remains admissible, and closure remains forbidden. A different
safe strategy must preserve the contract and receive independent validation;
exhaustion itself is not a user-authored requirement change.

### Level D: steering, load, and recovery

Deliver multiple messages while a worker is active, apply real semantic
steering at more than one stage, stop and resume the same task, and prove:

```text
resume -> read_state -> read_continuations -> queued steering -> revised plan
```

No dropped/reordered decision, new task, stale publication retry, timeline
substitution, or unexplained replay is accepted. The steering response must
identify every stale semantic node/task name. For each matching agent still
active in the native projection, one coordinator `interrupt_agent` call must be
observed. If publication wins the race, its first call must return
`state = superseded` and `published = false`, create no report, consume no
terminal slot, and require no corrective call. That response is counted as a
clean non-publication result, not as publication success or a tool error.
`SubagentStop` without the durable stale state and native reconciliation does
not satisfy this level.

Let the stale worker modify the shared checkout immediately before interruption
and prove the new revision remains behind `reconciliation_required`. No planner,
mutator, audit, or verifier may start until every stale task is quiescent and
the reconciliation worker seals the observed successor baseline. The test must
prove that stale filesystem effects are preserved as evidence rather than
silently attributed, discarded, or accepted as current implementation.

Resume with one continuation whose protected task name is absent from a
complete successful `list_agents` projection and prove loss recovery becomes
admissible only after confirming no finalized publication. Repeat with a
failed or ambiguous native projection and prove Cortex neither declares loss
nor duplicates the assignment.

Exercise a decision-bearing candidate family and prove that one plan-review
response atomically selects the semantic delta, advances the revision, binds
and approves only its validated graph, and grants no authority to unselected
alternatives.

Exercise one genuine pre-plan branch whose alternatives cannot all be validated
before selection. Prove `open_steering` precedes the rendered question,
`record_steering` applies a non-empty semantic delta, and low-risk planning
continues without a duplicate review. A no-op steering delta must fail in the
contract level and must never appear in live qualification.

### Level E: shortened real CLI E2E

Run an ordinary small product task through candidate plan, independent graph
validation, approval when required, implementation that seals an artifact
generation, one generation-bound dependent audit, fixes if needed, independent
documentation updates when needed, final verification and documentation-impact
assessment bound to the same latest generation, result presentation, closure
review, and close. Inspect the coordinator pane and every worker event stream.
Zero first-call Cortex errors are allowed.

Force one derived human-view write/readback failure after durable publication.
Prove receipt reconciliation repairs only the projection, returns one verified
server-generated link, and does not create another report or require the model
to reconstruct a path.

### Level F: full CLI qualification

Run one final all-tools/all-profiles CLI scenario only after Levels A-E pass on
the frozen payload. It must naturally create every operation precondition,
exercise all 22 profiles, include multiple in-flight messages and multiple real
steering revisions, prove readiness ordering and recovery, and finish through
the mandatory closure review.

Binary acceptance requires:

- all 20 operations observed successfully;
- all 22 profiles observed with their intended model, effort, scope, and
  terminal kind;
- no first-call error, corrective publication, premature assignment,
  stale/outcome mismatch, unexpected replay, missing operation, hidden worker
  error, or unexplained host interruption;
- explicit clean process completion.

### Level G: full Desktop qualification

Without changing any installable payload byte after the successful full CLI
run, repeat the full scenario in the real disposable Desktop development host.
Desktop must fully complete; merely launching it or observing a partial flow is
not a pass. The same binary criteria apply, including recovery and closure.

Any payload edit invalidates both host results and restarts qualification at the
smallest affected local level, followed by a fresh full CLI/Desktop pair.

## 11. Completion checklist

### Revised obligation-preserving design

- [ ] Qualify HostProbe and CLI/Desktop adapters against real native hosts.
  - [x] Implemented passive capability evidence, snapshot freshness, default
    model routing and observation-preserving injected adapters. On
    `b842bf8187b73e8d`, 66 focused tests and 4 subtests passed including source
    inbox, immutable registry, current storage and host boundary; 8 preflight
    tests then passed including passive snapshot export. Fixture transports do
    not qualify actual native calls. Unknown capabilities remain unverified.
  - [ ] Bind actual host transports and capture a snapshot at qualification start.
    - [x] Current dispatch encoding now passes through
      `CodexHostAdapter.prepare_spawn`; raw native list parsing moved into the
      host boundary and the existing signed hook observation path consumes it.
      No duplicate parser remains in native observation storage. On
      `f430b69545f8727d`, **75 focused tests and 113 subtests passed** across
      dispatch, routing, boundary, hook observations, loss recovery and filesystem
      policy. Other native operations still require live observation/binding.
    - [x] Exported the installed CLI 0.153.0 app-server protocol through its
      supported schema-generation command. The Desktop boundary now decodes its
      current `collabAgentToolCall` item for all five coordination operations,
      rejects old/unknown/foreign/incomplete items and preserves ambiguity.
      **45 focused tests passed** on `41042a8f2a0167d0`; the initial fixture
      helper had a duplicate Python argument and was corrected without changing
      runtime validation. These are schema/source tests, not a connected Desktop
      stream. The [official app-server documentation](https://learn.chatgpt.com/docs/app-server)
      describes item events, but the installed schema determines the decoder's
      exact supported shape. Client request schema does not expose a direct
      native-agent invocation endpoint; model-owned native calls remain required.
    - [x] CLI and Desktop launchers now capture a passive snapshot before native
      startup using the verified isolated candidate receipt. On
      `eaa89918573e0078`, 78 targeted tests and 113 subtests passed for adapters,
      snapshot launch, preflight, Desktop launcher, filesystem policy and sync.
      Actual native transport bindings remain pending. The preceding full run
      on `b842bf8187b73e8d` failed two filesystem-capability tests (800 passed);
      the new writer is now explicitly registered and rejects non-private or
      symlinked directories. Full regression on `eaa89918573e0078` then passed:
      **807 tests and 280 subtests**, 156.44 seconds. After the CLI-version
      support-script refinement, **25 targeted tests passed**. A passive capture
      against the real isolated candidate succeeded and recorded CLI 0.153.0;
      this is environment collection, not native E2E or Desktop qualification.
      Prompt contract lint, source release validation and marketplace metadata
      validation passed. Read-only sync check reports the intentionally untouched
      stable installation at 1.15.3 rather than this 1.15.6 candidate; it exits 1
      and is not recorded as an installed-plugin parity pass.
  - [ ] Verify input provenance, concurrency and recovery on both real hosts.
    - [x] Removed the duplicate activation-owned resume-binding writer. On
      `5ecbae960bdf68f0`, **57 focused tests passed**, including an actual hook
      subprocess using installed-cache topology and an existing lifecycle
      binding; the hook preserves that binding unchanged.
    - [ ] The follow-up CLI probe on `5ecbae960bdf68f0` in
      `/tmp/cortex-input-observer-recheck.JBHvuU` captured all three exact
      submitted messages in order, without the prior input-hook failure.
      Initial worker consumption and first publication succeeded. Two genuine
      additive changes reached revisions 2 and 3, but the second invalidated
      an active assignment; reconciliation then failed `assignment_not_ready`.
      The native list hook supplied JSON text with tagged completed statuses,
      while the adapter expected an object with string statuses. The session
      was stopped with `--interrupt`; this is not an E2E pass. The first change
      arrived after initial closure review, not during active worker execution.
      Input consumption and human-origin authentication remain unverified.
    - [x] On `0a3a043783e21f21`, **66 focused tests passed** for signed native
      observations, hook integration, adapters, typed reconciliation/recovery,
      and input capture. Bounded native JSON text and tagged completed statuses
      are now normalized; interrupted/unknown states remain non-quiescent.
      Duplicate keys, oversized/invalid envelopes and ambiguous states grant
      no authority. Terminal host status proves quiescence only, not fulfillment
      of a requirement. Real-host requalification remains pending.
      The full local suite on this payload passed: **867 tests and 280
      subtests**, 167.79 seconds. This does not qualify either real host.
    - [ ] The `0a3a043783e21f21` CLI probe in
      `/tmp/cortex-native-list-recheck.dHHMXf` recorded both additive steering
      messages and retained all three requirements. No tool failure was observed
      before operator interruption, but reconciliation did not finish: the
      adapter treated a fresh list's interrupted status as still active, while
      native wait does not complete for that status. The run was stopped with
      `--interrupt`, not counted as successful. Codex 0.153.0 derives interrupted
      from an aborted turn, separately from its final-result predicate; see
      [the versioned status implementation](https://raw.githubusercontent.com/openai/codex/rust-v0.153.0/codex-rs/core/src/agent/status.rs).
      The corrective boundary accepts an interrupted status only from a fresh
      complete signed list, never an interrupt reply or a filtered projection.
      On `a0eb414f9afa354b`, **67 targeted tests passed** (6.41 seconds),
      including signed hook observations, negative filtered/acknowledgement
      cases, typed reconciliation/recovery and source input capture. Source
      package validation passed for 122 files; live requalification is pending.
    - [ ] On `a0eb414f9afa354b`, the CLI probe in
      `/tmp/cortex-quiescence-recheck.RnLJp8` successfully captured a fresh signed
      complete list with an interrupted child, and current scope correctly
      exposed the reconciliation node as ready. The coordinator then selected
      the incompatible public recovery flag for that ready node; admission
      rejected it as `assignment_not_ready`. The exact session was stopped
      with `--interrupt`. No live pass is claimed. This motivates removing
      caller recovery-mode selection, not bypassing prerequisite checks.
    - [x] Removed public caller-selected recovery mode. The server derives it
      from observed node ownership and still validates signed loss evidence,
      exact scope, budgets and transactional lineage. On `8e5de756e4f9a75b`,
      **85 tests and 234 subtests passed**, including real stdio contract calls,
      recovery and first-call checks. The removed flag is rejected, not retained
      as a compatibility alias. The first candidate preparation exposed an
      outdated validator allowlist; it was corrected. A prior test run on
      `6b1e49555f9c1056` failed the generated required-property suffix check
      (85 tests and 233 subtests passed); the description now uses the canonical
      renderer instead of appending text after its required-property list.
    - [x] Current state, continuation and scope reads now consume the same
      verified native observation. On `1152e8add7c072b7`, **65 tests and 121
      subtests passed** (8.59 seconds). Tests prove that a stopped revoked
      worker is projected as quiescent and reconciliation as ready without
      altering persisted ownership. Admission still revalidates the observation
      and current epoch. The full regression and native continuation check
      remain separate gates.
      Full local regression on the same payload passed: **869 tests and 280
      subtests**, 169.00 seconds. Source release validation (122 files) and
      prompt contract lint passed. Native requalification remains pending.
    - [ ] The `1152e8add7c072b7` CLI probe in
      `/tmp/cortex-derived-recovery.d1kbBN` reached active-worker steering, but
      the hidden worker event stream showed a first-publication validation
      failure in nested verification strategy assessment (`bounded_length`).
      The exact session was stopped with `--interrupt`; this was not a declared
      injected fault and is not a pass. Do not weaken that evidence field to
      obtain a passing old-interface run. Resume the required small-operation
      publication redesign before broad qualification; the host fixes have
      source evidence but no completed native qualification yet.
    - [ ] Input-delivery CLI probe on `8148994d64988560` in
      `/tmp/cortex-input-observer-cli.RDBU81` failed: the real host required
      review of the newly registered input callback, then displayed hook exit 1
      after the submitted prompt. Only the exact callback was trusted in the
      isolated profile. The obsolete activation-owned resume-binding writer
      called an undefined helper on the installed-cache path; lifecycle already
      owns that binding. The exact smoke session was stopped with `--interrupt`.
      This is a failed adapter probe, not a short/full E2E or Desktop result.
    - [ ] Short CLI attempt on `f430b69545f8727d` in
      `/tmp/cortex-host-boundary-cli.fk0GQw` failed qualification: initial task,
      assessment, assignment, native spawn hook and worker assignment consumption
      succeeded; the first worker publication failed schema validation at
      `$.unresolved` (required field). The exact smoke session was stopped with
      `--interrupt`. No clean E2E, full matrix or Desktop pass is claimed. Do not
      weaken required publication evidence to accommodate this failed call.

- [x] Recorded the latest user-directed design and qualification requirements
  separately from historical implementation evidence. Stopped the old short CLI
  in `/tmp/cortex-typed-cli-integrity.O6zizx` on user steering; it is not a pass.
- [ ] Replace monolithic worker publication with incremental verified facts and
  separate narrative finalization.
  - [x] Added assignment-bound immutable execution receipt and declared-check
    note storage. On `b2be597259410621`, **26 tests and 4 subtests passed**
    (2.07 seconds), covering this journal, extraction drafts and maintenance.
    Notes survive an independently failed report transaction; changed retries,
    unknown checks, missing/wrong-key receipts and stale authority are rejected.
    Stdout observation, interrupted execution and actual exit status remain
    distinct; all notes explicitly remain unverified as requirement completion.
    The initial test run had 25 passes and one test error: the storage wrapper
    intentionally classifies SQLite exceptions, so the immutability assertion
    was moved inside the tested transaction without changing runtime guards.
  - [ ] Wire trusted native execution receipts, server-issued check selections,
    scalar public operations and narrative finalization; remove the monolithic
    public publication interface. Internal fixture ingestion is not host proof.
- [ ] Preserve immutable source requests and authenticated user-change events.
  - [x] Registered the previously unwired root-input event and connected passive
    private inbox capture. On `8148994d64988560`, **58 focused tests passed**
    across actual hook subprocess invocation, activation, preflight and source
    CLI/Desktop contract fixtures. Repeated messages remain separate; normal,
    help, worker, malformed and symlinked inputs are excluded. Normal selection
    now leaves the route instead of reactivating it. These are source integration
    tests, not native delivery or human-origin qualification. The first candidate
    preparations rejected outdated local validator event/handler allowlists;
    both now require the exact registered in-plugin callback.
    Full regression then passed on that payload: **858 tests and 280 subtests**,
    174.48 seconds. Prompt contract lint, marketplace validation, source release
    validation (122 payload files) and `git diff --check` also passed.
    The launcher snapshot now records declared candidate hook events without
    claiming delivery. **8 launcher tests passed**, and passive collection
    against the actual isolated CLI configuration succeeded. An initial targeted
    command named a nonexistent Desktop test file and ran no tests; the corrected
    command used `tests/test_desktop_live_dev.py`. No native scenario was run.
  - [x] Inbox consumption now requires an exact initial source binding before
    changes, decisions or informational messages. Their session must match the
    initial source session. Foreign-session rejection preserves the queue and
    task history atomically. On `a3c368744db58852`, **28 focused tests passed**
    for source ordering, immutable obligations and closure. Host-authenticated
    bootstrap integration remains pending; this gate alone proves no authorship.
  - [x] Source inbox primitives on `2379fd9aff8f09fb` passed **45 focused tests
    and 4 subtests**, including storage/maintenance regressions. Identical inputs
    remain distinct, consumption is ordered and idempotent, failed transactions
    leave no partial source, and signed records reject a wrong key/session.
    The real host-input adapter and public source bootstrap are not connected yet;
    these are source-level guarantees, not verified human authorship or live E2E.
  - [x] On `531d3f941e7fdf5e`, **23 focused tests passed** covering immutable
    original request bytes, criteria, revision history, irreversible retirement,
    direct user replacement and current-only storage. Database triggers reject
    rewriting/deleting history; reopening requires the guards. This does not yet
    prove host authentication or source-to-registry completeness.
- [ ] Separate versioned obligations/criteria from mutable plan revisions.
  - [x] Canonical registry snapshots passed with source/history/DAG checks:
    **46 tests** on `5d5e17c493c67738`. Snapshot digest includes the immutable
    source, requirements and criteria, remains unchanged by plan events, and
    changes after direct recorded steering. Equal acceptance/verification text
    keeps distinct identities. Public source-review binding remains pending.
- [ ] Audit source-to-registry completeness and reject uncovered source material.
  - [x] Exact Unicode source anchors and structural extraction audit passed with
    the immutable-history/candidate/storage checks: **43 tests passed** on
    `9b546962eec062e2`. Unmapped source passages and registered criteria are
    reported; stale anchors and unknown subjects are rejected. Duplicate links
    do not add evidence. Full-request character coverage deliberately grants no
    semantic approval. Runtime admission and independent audit binding pending.
  - [x] Full source regression on `9b546962eec062e2`: **772 tests and 280
    subtests passed**, 151.99 seconds. This predates the next revision-admission
    guard and is not native or revised-interface qualification.
- [ ] Enforce obligation preservation across errors, retry, replan and recovery.
  - [x] `4f0cac198cc4a109`: **65 focused tests passed** for immutable original
    constraints/check expectations, append-only requirement history, contiguous
    revisions, one revision per recorded decision and rejection of retirement
    without a task-bound recorded decision. User replacement and candidate
    selection still pass. Current decision attribution is not yet independent
    host-source proof; no end-to-end guarantee is claimed.
- [ ] Replace large nested inputs with small operations, without legacy adapters.
  - [x] Initial incremental extraction storage passed **32 focused tests and
    4 subtests** on `5c5b1f54d322f7eb`: scalar requirement/criterion/range writes,
    exact reconciliation, interrupted-write rollback, session/source isolation,
    immutable history and rejection of an old draft after registry revision.
    These internal primitives do not yet replace the public catalogue.
  - [x] Full source regression on `8c17583abd95a6aa`: **842 tests and 280
    subtests passed**, 170.49 seconds. This predates the input-hook connection
    and remains source evidence rather than revised-interface/live qualification.
  - [x] On `8c17583abd95a6aa`, **46 focused tests and 4 subtests passed**
    for extraction, source anchors, inbox and storage. Corrections are append-only
    and bound to the observed draft content; exact late reconciliation preserves
    newer edits. A further **14 extraction tests passed**, including four racing
    corrections (one commit), required SQL relation guards, and fail-closed
    store/maintenance checks after guard removal. Content and mapping digests
    are distinct; neither alone claims semantic review or execution authority.
  - [ ] New durable extraction core is being connected: one requirement,
    criterion or exact source link per transaction; task/session-bound source,
    bounded storage and resumable exact reconciliation. Drafts deliberately
    cannot seal requirements, activate execution or authorize closure. Public
    bootstrap, independent semantic review and catalogue cutover remain pending.
- [ ] Derive assignment/revision/terminal authority entirely from server bindings.
- [ ] Separate Markdown reports from current-generation verification evidence.
- [ ] Integrate bounded retry, strategy change and safe autonomous repair.
- [ ] Enforce current-evidence coverage and fresh explicit closure permission.
  - [x] Removed incomplete-task closure and the retired delegation hook alias.
    On `6a02b6b70a299931`, **67 focused tests and 121 subtests passed**.
    Both supported closure verdicts require complete current evidence; a close
    answer cannot waive unfinished requirements. Negative tests leave closure
    storage untouched; a completed graph still closes with verified links and
    rejects changed replay payloads. This is not revised-interface/live evidence.
- [ ] Qualify every new tool and all 22 profiles locally, including negative cases.
- [ ] Pass short real CLI before final qualification.
- [ ] Pass full CLI with parallelism, steering, resume and declared fault recovery.
- [ ] Pass full Desktop on the same unchanged payload.

### Historical implementation evidence (not revised-interface qualification)

This checklist is the ongoing implementation record. Update it after each
completed implementation or verification step, not only at final handoff.
Check a parent item only when its entire guarantee is implemented and verified;
record completed substeps and remaining integration or qualification beneath
an unfinished item. Source tests never count as live qualification. When a
later change invalidates evidence, reopen the affected item and record the
required rerun.

- [ ] Remove all obsolete runtime contracts and compatibility paths.
      - [x] Storage now creates only schema 2 directly and rejects old or unknown
        schemas without conversion. Removed migration/backfill loaders and old
        native-name adapters. The current-storage, graph, public API, assignment,
        remediation and command/transport suites passed 90 tests and 113 subtests
        on cache suffix `e0a6f2d931f9c2c4`; this is source evidence only.
      - [ ] Remove old report/service/rendering routes and migrate their tests.
        - [x] Full source regression on `c0d2e9023a20659c`: **751 tests and
          280 subtests passed** (150.56 seconds), including current-only
          governance storage, projection recovery and updated bundled policies.
          Native qualification remains open.
        - [x] Removed initiative storage, lookup, maintenance, service and
          projection routes; current governance/timeline column sets reject old
          shapes without migration. `dd6184721e53b0ae`: **119 tests and 14
          subtests**, plus **22 storage/locator/registry tests and 7 subtests**.
        - [x] Removed competing initiative/prose-pipeline guidance and harvest
          progress-report publication policy from the installable package.
          `c0d2e9023a20659c`: **81 focused policy, renderer, storage and registry
          tests passed**. Initial baseline observation and later discovery are
          distinct; terminal publication remains assignment-typed and singular.
        - [x] Removed the unused clarification-continuation renderer and
          initiative service entry points. Together with bounded transient
          projection repair, **17 focused tests passed** on
          `511d57f223faf98f`. Storage-level initiative removal is subsequent
          work and is not covered by that checkpoint.
        - [x] Removed the obsolete service-level decision and governance
          mutation entry points and the store's old governance writer.
          Decision recording now requires the aggregate's existing transaction
          and exact binding; there is no fallback to caller-keyed mutation
          receipts. Migrated binding/race/rollback tests to DecisionAggregate.
          Focused decision, public API and current-storage checks passed
          **74 tests and 14 subtests** on suffix `dbd8f9d182710278`.
        - [x] Removed four unused host-delivered worker-question routes and
          the assignment-read fallback that inferred terminal ownership from
          old outcome lists. Missing typed scope now fails closed. Current
          storage, assignment, runtime, public API and real-hook/source parity
          checks passed 85 tests plus 14 subtests on suffix `626998b3b903ec5e`.
          Stable installation was untouched; only the isolated candidate and
          source cache suffix changed. Remaining storage scaffolding and
          other compatibility routes still need removal.
        - [x] Replaced legacy timeline/backfill fixtures with eight passing
          typed chronology regressions on suffix `6e74064714997f9d`: validated
          ready-node native-name isolation, review/revision chronology,
          concurrent WAL ordering, rollback, one publication event, foreign
          scope rejection, SQL-only history and managed-symlink safety.
        - [x] Removed implicit outcome-grouping fallback from store/service task
          creation; linked outcome contracts are mandatory. Thirteen internal
          fixtures now supply exact grouping explicitly. Storage, provenance,
          receipts, locators, binding, maintenance and typed recovery suites
          passed: 83 tests plus 128 subtests on suffix `cd3e5523910dbe76`.
        - [x] Removed ten unreachable legacy predecessor, prose-plan readiness,
          report-digest, lease-replacement and clarification-publication methods.
          Current typed storage, admission, publication, recovery, first-call
          and source hook suites passed: 52 tests plus 99 subtests on suffix
          `555419e803159340`. This does not complete the remaining old test and
          runtime-route cleanup.
        - [x] Removed untyped assignment creation, chunked publication and the
          previous domain-report publisher from store/service, along with their
          obsolete MCP shape hints. 105 tests and 113 subtests passed on suffix
          `f622671411a693f0`; remaining older tests still require migration.
        - [x] Removed directory migration and old report-column sentinels;
          20 source projection/storage/publication checks passed on suffix
          `1548862b0503eb4c`. Old directories are neither moved nor adopted.
        - [x] Replaced legacy presenter envelopes, chunk-merging fallbacks and
          compatibility wrappers with current typed plan/result/documentation
          views. Plans display every node, dependency, capability, expected
          check, remediation strategy and finite budget; report observations
          come only from node coverage. Unknown formats fail view creation.
          80 tests and 113 subtests passed on suffix `59939f53ef2bb3d3`,
          including first-call source MCP and projection repair. No native
          live result is claimed.

- [ ] Fresh execution enforces `open_task`, then one governance assessment,
      before the first assignment.
- [ ] Every mutation atomically stores its transition, timeline event, and
      server-derived command receipt.
      - [x] Direct user steering now creates and consumes its decision binding
        in the same command transaction as contract revision, stale ownership
        and its receipt. Failed/no-op/empty/colliding deltas and an injected
        transaction failure leave no binding, decision or revision behind.
        Canonical complete-delta validation is shared with candidate proposals;
        point replacements preserve position and never merge old fields.
        Focused suites passed **135 tests and 134 subtests** on suffix
        `0b20b44e46e26654`. The full source suite then passed **686 tests and
        265 subtests** on that unchanged payload. Real-host qualification
        remains due; these source results are not live evidence.
      - [x] Cross-task governance regression and artifact-boundary transitions
        passed 14 focused tests on cache suffix `8af120b1371da8ae`. Rebaselining
        compares nested observations within each method/path boundary, including
        two tasks sharing one project; actual file-helper subprocesses are
        covered. This is source evidence, not native live qualification.
      - [x] Typed store publication commits graph state, report/chunk, timeline,
        and terminal receipt together; injected report-insert failure rolls
        back the generation and graph transition. Typed projection failure
        preserves that commit and repairs without duplication on exact retry.
        `tests/test_typed_publication_transaction.py`; 110 focused tests passed
        on cache suffix `e9068de177e02968`. Public routing remains unfinished.
- [ ] Identical ambiguous retries reconcile; changed retries conflict; explicit
      successes are never replayed as new mutations or native spawns.
      - [x] Command receipt uniqueness and every lookup/reconciliation path now
        include project, aggregate type, aggregate identity and operation. Equal
        local slots cannot replay another task or command; both transactional
        receipt variants are covered. Nineteen focused tests passed on cache
        suffix `dbe1698593933320`. The broader old transport suite still requires
        typed-contract migration and filesystem capability registration; it did
        not pass and is not qualification evidence.
      - [x] Typed store dispatch uses an immutable admission-read snapshot for
        its receipt: identical retry preserves one delegation, capability,
        node owner and native dispatch; changed retry conflicts. Covered by
        `tests/test_node_assignment_receipts.py`; public/host integration remains.
- [ ] Minimal/light plans proceed informationally unless a real decision,
      credential, external authority, or explicit review is present; full and
      material-high-risk plans cannot execute before plan review.
      - [x] The selected minimal route generates a complete-contract execution
        node only after successful baseline publication. Both read-only and
        mutating routes use typed claims, terminal coverage and closure evidence;
        full/risky/requested-review minimal routes are rejected atomically.
        Explicit review preference persists across reassessment. Initial narrow
        regression: 28 tests passed on cache suffix `545e86c673db33b5`.
        Host qualification and final regression remain outstanding. The broader
        typed regression subsequently passed 160 tests on the same payload,
        including low-risk explicit-request plan-review admission.
- [ ] Complexity alone can increase model or verification depth but cannot
      force `full` governance or another user review.
- [ ] Typed plan DAG is stored, revision-bound, and validated.
      - [x] Pure structural validation and revision-bound SQLite graph storage
        exist in `execution_graph.py` and `graph_ledger.py`; focused tests use
        the real project-sharded store.
      - [ ] Replace the public prose-stage/old assignment route with typed
        graph bootstrap, node selection, and publication integration.
        - [x] Public scope/assignment and all three publication schemas now
          use typed graph nodes; removed obsolete domain-level scope inference,
          prose-stage admission, and report-envelope adapters. Public regression
          covers baseline, planning, independent validation, explicit approval,
          and steering/recovery (`tests/test_typed_public_api.py`).
        - [ ] Complete minimal graphs, reconciliation/remediation, remaining
          read/closure integration, bundled instructions and old test migration.
- [ ] Contribution ownership is unique, composite outcome expressions are
      satisfiable, and independent verification is many-to-many.
      - [x] Migrated coverage/outcome regressions to the typed public API:
        13 tests passed on suffix `555419e803159340`. They cover all-of
        contribution completion plus both dependent audits, profile-independent
        ownership, simultaneous public assignment claims, invalid-coverage
        rollback, immutable superseded scope and truthful incomplete closure.
- [ ] Required and produced capabilities are reachable through explicit edges.
- [ ] Ready/waiting projection follows the complete predecessor-status matrix.
      - [x] Scope projection and atomic assignment admission now agree on the
        shared-checkout mutation barrier, including bootstrap/generated nodes
        and reconciliation's verified quiescence exception. Parallel compatible
        readers remain admissible. Focused regression: 51 tests passed on
        cache suffix `b00efcd8f08d5b57`; native-host qualification is pending.
- [ ] Assignment admission rejects unmet predecessors atomically.
- [ ] Concurrent assignment claims cannot duplicate node ownership.
      - [x] A two-thread SQLite claim race admits one owner and rejects the
        competing claim in `tests/test_graph_ledger.py`.
      - [ ] Exercise that guarantee through the integrated public API and hosts.
        - [x] Public same-node claim races passed in the typed coverage suite.
          Five additional public API cases plus two subtests passed on suffix
          `555419e803159340`: independent ready-node fanout after graph
          validation, reverse-order worker consumption/publication, exact
          scoped evidence, rejected foreign reconnect and chronological reads.
          Native host coverage remains pending.
- [ ] Semantic readiness remains distinct from native slot capacity; capacity
      waits neither alter the DAG nor create duplicate assignments or user
      questions.
- [ ] Shared-checkout mutation admission allows only one active mutator while
      retaining safe parallel artifact-independent and read-only work.
      - [x] Transactional graph-layer tests reject a second mutator and admit
        parallel audits after implementation; public integration remains open.
- [ ] Every artifact-dependent result is bound to one sealed generation and
      read-only evidence proves matching start/end fingerprints.
      - [x] Worker-owned Git/path-manifest fingerprint procedures and focused
        tests cover content, index, permissions, absence, ignored declared
        artifacts, and symlink boundaries (`tests/test_artifact_fingerprint.py`).
      - [x] Worker manifests now retain all fingerprint inputs, including the
        canonical artifact boundary and Git state. Owner-private,
        content-addressed manifests outside the project can be independently
        reloaded to recompute changed-path commitments; tampering, boundary
        changes, and hidden HEAD-only mutations are rejected/detected. Focused
        artifact/publication/assignment/graph checkpoint: 38 source tests passed
        on cache suffix `73c475a92270f4b2`.
      - [ ] Deliver the procedure and observations through the typed public
        assignment/publication contract and qualify real worker use.
- [ ] Artifact-dependent workers leave no unowned background mutator after
      their final fingerprint or publication.
- [ ] A mutation seals a successor generation and invalidates all dependent
      evidence bound to older generations.
- [ ] Snapshot conflict is a static no-report/non-slot result and routes
      deterministic reconciliation.
      - [x] An unstable initial baseline returns `snapshot_conflict` without
        creating a report, terminal slot, or artifact generation; source tested.
- [ ] Assignments select current semantic node keys rather than ambiguous
      outcomes.
- [ ] Worker scope and predecessor evidence are rendered from the graph rather
      than repeated in coordinator prose.
      - [x] Typed store assignments expose their immutable node scope through
        worker `read_task`, including expected terminal kind and artifact
        procedure; a writer-profile discovery assignment correctly exposes
        result publication. Source checkpoint: 105 tests on `9d7a6556d84be6e8`.
      - [ ] Remove the old public assignment/publication route after wiring
        the complete typed protocol and migrate its contract tests.
- [ ] Discovery, planning, and graph validation use an append-only bootstrap
      graph with bounded creation rules.
      - [x] Task creation seeds the bootstrap graph; baseline precedes discovery,
        and planning waits for completed discovery. Candidate publication
        derives planned checks without observed-verification facts. Covered by
        `tests/test_graph_ledger.py`; public protocol integration remains open.
- [ ] Minimal tasks use generated graphs rather than graph-free admission.
- [ ] Nontrivial candidate graphs require independent semantic validation.
      - [x] Opening review now checks the current candidate's completed
        independent validation inside the decision transaction. Premature
        opening leaves no decision binding. Verified packet, typed API,
        projection repair, closure and all-operation source checks passed:
        34 tests plus 99 subtests; seven additional review/parallel/steering
        cases plus nine subtests passed on suffix `6e74064714997f9d`.
      - [x] Graph storage creates a candidate-bound validation node; delivery
        stays waiting until its complete result, and failed validation leaves
        the candidate inactive. Source tests cover both paths.
- [ ] Delivery is impossible before exact candidate activation.
- [ ] Remediation templates expand deterministically and idempotently.
- [ ] Regression generations are append-only and preserve prior evidence.
- [ ] Remediation and strategy budgets are finite, progress fingerprints reject
      non-progress, and exhaustion terminates without invented success.
      - [x] Validated plans now bind a finite menu of repair strategies and
        causal checks. A stopped generation can expose one diagnostic and then
        independent strategy-validation node; unavailable, disagreement,
        unchanged artifact and exhausted strategy budget cannot spawn another
        repair. The next chain consumes the failed regression and both strategy
        reports. Eighty-eight focused tests passed on `49bd5d459ceea6f2`.
        Subsequent regression-check coverage changes require a fresh rerun;
        failed-repair recovery and full remediation qualification remain open.
      - [x] Generated regression now includes every original source check plus
        declared regression checks, preserving required checks. Same-strategy
        progress requires an explicitly passed prior finding on a changed
        artifact. Combined source/transport regression: 125 tests and 113
        subtests passed on cache suffix `a8811ce32891cf96`.
      - [x] Independent regression can measure a sealed terminal repair attempt
        even when that attempt reports incomplete evidence; an active attempt
        remains inadmissible. It does not trust the repair's success claim or
        restore capabilities until all independent checks pass. Original failed
        repair reports remain unchanged. Forty-seven focused tests passed on
        suffix `983f6fd847256fc8`.
      - [x] A confirmed ordinary in-contract audit finding creates one
        idempotent repair/regression pair from the validated policy. Regression
        waits for the repair; only its success derives a resolved source node,
        leaving the original failed report/facts immutable. Required independent
        classification does not silently authorize repair. Source checkpoint:
        36 focused remediation/graph/publication/assignment tests passed on
        cache suffix `07d885be9a21f16f`.
      - [ ] Complete classification verification, repeated generations,
        strategy/progress/exhaustion, and integrated public/host qualification.
        - [x] Independent classification-review nodes are generated before
          high-risk-policy repair. Executed review checks carry one structured
          source-finding assessment; ordinary checks cannot claim it. Agreement
          on an in-contract defect unlocks repair with both source and review
          evidence; disagreement or inconclusive evidence does not. Derived
          facts retain their coverage subject instead of merging equal check
          keys across outcomes. Focused checkpoint: 138 tests passed on cache
          suffix `31086fbeda2d22da`.
        - [x] Same-strategy regression can append an evidence-linked generation
          only with a changed artifact and fewer unresolved matching findings.
          New assignments consume the immediately preceding failed regression
          and original finding. Non-progress exhausts a single-strategy policy;
          later successful regression resolves historical failures in projection
          without modifying reports. Focused regression: 45 tests passed on
          cache suffix `d730acd90a819f83`.
        - [ ] Complete bounded disagreement recovery and strategy generations;
          this source checkpoint is not full remediation or live qualification.
          - [x] A classification disagreement creates a bounded follow-up
            consuming the original finding and preceding review. Repeated
            structured evidence (even with changed prose) yields derived
            `exhausted` state without rewriting the failed source or spawning
            another review. Focused source checkpoint: 88 tests passed on
            cache suffix `03031ab77c7545bc`; repair strategy generations and
            public/host qualification remain outstanding.
- [ ] Successful regression resolves blocking findings without rewriting the
      original failed report.
      - [x] Two public-API approved-plan flows passed on suffix
        `6e74064714997f9d`: delivery failure and dependent-audit failure each
        generate bounded repair and independent regression, reach complete
        coverage in revision one, retain the immutable failed publication,
        and leave exactly the original user decision. No repeat steering or
        plan question is manufactured.
- [ ] Scope-, authority-, and risk-changing findings cannot activate remediation.
- [ ] Terminal publication kind derives from node purpose, is exposed, and is
      enforced.
- [ ] Exactly one cross-kind terminal publication is enforced.
- [ ] Human-view links are returned only after render/readback digest parity;
      projection repair cannot duplicate the durable report.
      - [x] Plan publication commits its report before derived view IO;
        `tests/test_publication_projection_repair.py` passes injected failures
        before/after file creation and proves one report, one terminal slot,
        repair on identical reconciliation, and a verified review link.
      - [x] The same post-commit repair route covers result and documentation
        publications; all six before/after-write failure cases pass.
      - [x] Migrated all six failure cases to the typed public API, with actual
        baseline, planner, graph-validation, implementation and documentation
        prerequisites. Verified durable count/slot parity and repaired links
        after failures before/after view creation on suffix `9cc47ee9187ed6cb`.
      - [ ] Repeat through real CLI E2E on the final unchanged payload.
      - [x] `511d57f223faf98f`: all three terminal kinds recover one transient
        write/readback I/O failure inside the original request, using the same
        durable report and expected bytes. Persistent failures remain errors;
        unsafe paths, external edits and permission failures are not retried.
        Seventeen focused checks include exact report counts and retry bounds.
- [ ] Outcome coverage is the only public observed-verification source.
- [ ] Contradictory coverage status and verification facts are rejected.
      - [x] Typed coverage rejects incomplete dispositions containing only
        successful facts; non-applicable optional checks are distinguished
        from unrun checks requiring a finding classification. Source checkpoint:
        104 focused tests passed on cache suffix `4861b9b467cf6019`.
        Later payload changes require rerunning this selection.
- [ ] Steering returns its atomic invalidation and supersession effect.
      - [x] The steering transaction invalidates graph leases, creates the
        revised bootstrap graph, raises the project barrier, and returns exact
        protected task names and node keys. Typed public regression verifies
        superseded publication creates no report and recovery retains the
        stale nonquiescent route.
- [ ] Steering raises a project reconciliation barrier before stale workers can
      race further work into the revised graph.
- [ ] Stale native workers are reconciled by task name/node key and interrupted
      through `interrupt_agent` when still active.
      - [x] Private hook observations bind a complete unfiltered native tree
        to the creating coordinator, task, revision, and barrier epoch. Filtered,
        ambiguous, foreign-root, stale, or tampered observations cannot prove
        quiescence. Unknown worker states remain present. The graph-level
        read-only hazard projection does not infer loss of active assignments
        or mutate lifecycle state. Focused source checkpoint: 30 tests passed
        on cache suffix `09be5841e73d7380` (native observation, hook, typed public
        API, recovery connection gate, and graph ledger).
      - [ ] Integrate verified observations into atomic reconciliation admission
        and qualify actual native interruption and resume on both hosts.
- [ ] Revised planning and execution remain inadmissible until stale workers are
      quiescent and a read-only reconciliation publication seals the observed
      baseline generation.
      - [x] Typed API regressions cover steering both before and after a sealed
        baseline, native presence/absence, observation changes between scope
        read and claim, atomic quiescence recording, successor baseline sealing,
        and revised planning admission. Partial reconciliation cannot release
        the barrier. Concurrent audit snapshot conflicts share one pending
        reconciliation instead of consuming one attempt per auditor.
        Focused typed/kernel/worker-procedure checkpoint: 133 tests passed on
        cache suffix `fad721fff58c75da`.
      - [ ] Complete non-progress/exhaustion and cross-task reconciliation,
        then qualify this path with actual native workers in CLI and Desktop.
        - [x] Partial reconciliation now appends a finite successor carrying the
          preceding report and native-loss lineage. Repeated structured evidence
          exhausts the route; successful successor seals a generation and derives
          earlier incomplete attempts as resolved without changing their reports.
          Fifty-two focused tests passed on suffix `d23d8c5c078125bb`.
- [ ] A racing stale publish returns the static `superseded` and
      `published=false` variant without a report, slot consumption, retry, or
      tool error.
- [ ] Recovery ordering is connection-gated.
      - [x] Recovery exhaustion is a static successful non-dispatch result:
        confirmed lost authority is revoked, nodes become exhausted, no worker
        or report is invented, and closure cannot claim readiness. Mixed live
        ownership is not inferred away. Focused recovery/public/closure/transport
        regression passed 56 tests and 113 subtests on `613fb2d94c4044a5`.
      - [x] Fresh coordinator MCP connections require task opening or an
        existing-task state read; unfinished work then requires the terminal
        continuation page. Real source-stdio regression:
        `tests/test_recovery_connection_gate.py`.
      - [x] Ordinary same-connection state inspection does not manufacture a
        recovery hold; continuation counting and rendering preserve active
        assignments instead of dropping them in the identity filter.
      - [ ] Qualify recovery alongside typed graph reconciliation and native
        workers in the final CLI/Desktop scenarios.
- [ ] Recovery uses lifecycle-hook markers and explicit Cortex reads; it never
      relies on conversational memory or an MCP push.
      - [x] Both `resume` and `compact` set lifecycle recovery markers;
        continuation pagination and task binding are checked by
        `tests/test_activation_hook.py`.
      Source checkpoint: the focused graph/model/recovery/stale-publication
      selection passed 53 tests on cache suffix `d0df77057ac23fd6`.
      This is source/stdio evidence, not real-host live qualification.
- [ ] Resume loss requires a complete native agent projection plus absence of
      finalized publication; ambiguous native state cannot authorize replacement.
      - [x] Signed post-dispatch native absence/terminal evidence enables atomic
        loss revocation and a read-only reconciliation assignment. No report is
        fabricated for the lost worker. Only a successful successor baseline
        enables replacements, with original-owner lineage and reconciliation
        evidence. Multiple lost readers share one reconciliation and resume as
        parallel replacements. Focused regression: 44 tests passed on cache
        suffix `80ed6522481ae8d4`.
      - [ ] Complete exhausted-recovery disposition, cross-graph/task hazards,
        and real-host resume/parallel-loss qualification before marking complete.
      - [x] A replacement candidate cannot orphan active execution workers;
        planning readiness waits for their finalized evidence. The expanded
        typed regression passed 166 tests on suffix `9cc47ee9187ed6cb`.
- [ ] Revised plans continue without redundant review unless risk or authority
      changes.
      - [x] Unanswered plan packets are now bound to candidate identity,
        independent validation, artifact/barrier state and governance epoch.
        A changed risk boundary or artifact cannot consume the old approval;
        historical unanswered bindings no longer block current replanning.
        Direct user steering can atomically supersede a pending plan question.
        The two newly reproduced failures are fixed; the combined decision,
        family, source-MCP, receipt and closure suite passed **73 tests and 212
        subtests** on suffix `c01b9f00954ed7ad`. Full and native reruns remain due.
      - [x] Approval fulfills its governance decision boundary instead of
        making high-risk classification or requested review a perpetual replan
        question. Six public-API steering scenarios cover full/requested
        initial review, independent successor validation, informational
        continuation, and fresh risk/renewed-review boundaries. Focused suites
        passed **66 tests and 14 subtests** on suffix `9864ba76224fae73`.
        Candidate-family selection and real-host qualification remain open.
      - [x] Required plan approval atomically grants the exact validated graph;
        minimal and full public test cases verify readiness before/after review.
        Replan risk/authority policy and candidate-family selection remain open.
      - [x] Rejected candidates permit bounded autonomous replanning with the
        prior plan and independent rejection evidence; an identical rejected
        graph with different summary is not progress. A newer candidate revokes
        stale graph selectors within the same semantic revision. A newer full
        assessment invalidates older approval for subsequent admission without
        blocking bound workers. Generated selector namespaces are reserved in
        the advertised graph contract. Current focused checkpoint: 153 tests
        passed on cache suffix `b44a7d5930f241c4`.
- [ ] Decision-bearing plan review atomically selects one validated branch and
      grants no authority to alternatives.
      - [x] Public plan publication now uses complete candidates only; the old
        single-graph input was removed. Public review exposes exact alternatives
        and one response atomically selects a branch, applies its complete
        delta, advances revision and approves the independently validated
        graph. Unselected nodes are never installed. Original verification is
        referenced, not republished or copied into fabricated facts. Rollback,
        exact retry/conflict, empty-delta choice, stale artifact and selected
        boundary rebaselining tests passed: **35 tests and 99 subtests** on
        suffix `f6891c7d4bd4c941`. Full regression and native qualification remain
        outstanding.
      - [x] Family storage now retains all immutable proposed contracts and
        graphs without installing any alternative's execution nodes. The
        independent validator must cover five checks per alternative; complete
        validation yields decision-readiness, never outcome completion or
        execution authority. Selection evidence rejects changed artifact/base
        commitments. Focused source suites passed **79 tests and 4 subtests**
        on suffix `4cacf155a73a6650`. Public selection integration remains open.
      - [x] Added the immutable structural family validator with one shared
        semantic-outcome schema: exact base revision, base-contract/delta/graph
        digests, bounded alternatives and aggregate bytes, complete proposed
        contracts, no field merging, exact branch selection and rejection of
        duplicate/equivalent alternatives. Focused family, graph, assignment,
        runtime and storage suites passed 127 tests on suffix
        `f24c8644c5ebb396`. This foundation is not yet connected to public plan
        publication, independent family validation or atomic review selection;
        those integration steps and live evidence remain outstanding.
- [ ] Pre-plan steering is used only when an answer is required to construct a
      valid contract; no-op steering is rejected and never used for coverage.
- [ ] Every host-dependent transition is mapped to a currently supported
      Codex coordination call or hook event.
- [ ] Cortex profile selection remains server-bound assignment policy and is
      never sent as an unsupported native spawn role/profile argument.
- [ ] Every native Cortex worker uses supported zero-history spawn semantics
      with default Luna or an explicit Terra/Sol override and explicit effort
      no higher than `max`; qualification verifies the configured default is
      Luna, and native spawn never passes Luna explicitly.
      - [x] Native dispatch omits the model for Luna, passes Terra/Sol
        explicitly, and rejects explicit native Luna; focused source
        regressions cover these cases in `tests/test_model_routing_policy.py`.
      - [ ] Verify the configured default and actual native dispatch on both
        real qualification hosts using the final unchanged payload.
- [ ] No implementation path requires adoption of a dynamically refreshed MCP
      catalogue, spawn `updatedInput`, asynchronous hook-driven agent
      termination, backend invocation of host coordination, or unauthenticated
      `SubagentStart`/`SubagentStop` data.
- [ ] Plan approval gates orchestration only and never substitutes for native
      Codex permissions or unavailable credentials.
- [ ] Final verification and documentation bind the same latest sealed artifact
      generation before closure review.
      - [x] Public closure now uses typed graph/outcome evidence rather than
        legacy coverage. A current explicit close decision is necessary but
        cannot authorize a successful verdict for incomplete work. Review binds
        graph/artifact/publication state, including no-report snapshot conflicts;
        changed closure retries conflict. A completed public typed graph closes
        with verified publication links. Source checkpoint: 42 focused tests
        plus all 3 typed-closure tests passed on cache suffix `8ac329bcf1f78726`.
      - [ ] Complete all-generation/documentation/remediation edge cases and
        mandatory post-result closure review in real CLI/Desktop qualification.
- [ ] Closure claims only the latest observed sealed generation; qualification
      prevents external writes across the final-observation/closure interval
      instead of claiming an unavailable atomic filesystem seal.
- [ ] All 20 operation contract scenarios pass.
      - [x] Full source checkpoint on unchanged suffix `dbd8f9d182710278`:
        **719 tests and 258 subtests passed** in 150.18 seconds after removing
        the alternate decision/governance writers. README, SECURITY and feature
        documentation are being aligned to current typed behavior. Native
        qualification remains separate and unfinished.
      - [x] Full source checkpoint on unchanged suffix `036477d48fae188b`:
        **711 tests and 265 subtests passed** in 149.33 seconds. Subsequent
        decision-freshness and in-flight steering fixes require a new full run;
        this result does not certify those changes or native live behavior.
      - [x] On suffix `c01b9f00954ed7ad`, broad regression reported **714
        passed, 265 subtests passed, 1 failed**. The failure was a documentation
        requirement for the explicit mandatory final closure review; that
        wording was restored. Focused documentation/host-policy/review checks
        then passed **67 tests**. Later payload cleanup requires a full rerun.
      - [x] Corrected catalogue reserve regression without weakening the limit:
        complete input catalogue is **61,334 bytes**, with **4,202 bytes** free.
        The source-MCP matrix now also selects an independently validated
        candidate-family branch and validates its revision-effect output.
        **22 tests and 99 subtests passed** on suffix `036477d48fae188b`.
        The preceding suffix `f6891c7d4bd4c941` had 710 passing tests and 265
        passing subtests but failed the catalogue reserve gate; it was not a
        clean qualification result. Native CLI/Desktop remain unrun.
      - [x] Removed the parallel legacy owner-claim/coverage tables and outcome
        assignability calculation, obsolete profile-derived publication policy,
        assignment revision fallback and alternate governance-closure API.
        Internal inspection now derives completion from the same typed graph as
        public state. Focused storage, minimal execution, assignment, recovery,
        closure and maintenance checks: **55 tests and 4 subtests passed** on
        suffix `17248520c2568844`; full suite and native qualification still due.
      - [x] Full source checkpoint on suffix `d2dad7cf2677dbfd`:
        **681 tests and 262 subtests passed** in 142.18 seconds, including
        rejection of legacy partial-field steering without mutation and
        coordinator-only decision storage. Later payload edits invalidate this
        as the final qualification checkpoint; native live remains unrun.
      - [x] Removed the obsolete worker clarification delivery relation from
        current storage, decision bindings, kernel and public callers: no worker
        assignment, delivery capability, delivery states or delivery index remain.
        Source storage/maintenance, decisions, closure and all-operation scenarios
        passed **28 tests and 4 subtests** on suffix `bdd9513297bbf191`.
      - [x] Updated coordinator/control/adaptation and supporting communication,
        recovery and validation instructions to typed node authority, immediate
        steering, signed native recovery, finite corrective work and purpose-bound
        publication. Removed prose-only outcome reconstruction, worker-authored
        finding identities, retry handles and automatic review for uncertainty.
        Focused policy/bootstrap/worker tests: **66 passed** on suffix
        `911ab961f61ee749`. This is source evidence, not native qualification.
      - [x] Recorded the intermediate full source result on suffix
        `8289c283a1f65b7f`: **678 passed, 258 subtests passed, 1 failed**.
        The failure asserted obsolete activation-policy wording; the focused
        regression now checks typed identity/artifact/coverage safeguards.
        A new full run is required after subsequent installable edits.
      - [x] Full source checkpoint on unchanged suffix `6e74064714997f9d`:
        **652 tests and 258 subtests passed** in 142.31 seconds. The previously
        failing domain, timeline, runtime-remediation and planner-policy
        fixtures are migrated. This proves the current implemented surface;
        it does not complete outstanding candidate-family/legacy-removal
        requirements or native CLI/Desktop qualification. Subsequent payload
        edits require a new stamped validation run.
      - [x] Runtime-remediation fixture migration is complete: all 21 tests
        pass on unchanged suffix `6e74064714997f9d`. This includes signed
        native-loss evidence, exact grouped recovery, sibling/parallel
        ownership, immutable receipts, UTF-8 aggregate limits, first-text
        typed authority and multi-report pagination. Removed the unused
        legacy assignment/publication helpers. These are source tests, not
        actual native CLI/Desktop qualification.
      - [x] Two runtime authority regressions pass on unchanged suffix
        `6e74064714997f9d`: genuinely bounded contract additions produce
        multiple assignment pages; recovery preserves exact page receipts
        and permits one typed publication. Steering between pages revokes
        reading authority without publishing or admitting a replacement
        before native quiescence. No oversized historical rows are injected.
        The remaining runtime-remediation suite is not clean: 12 failed,
        9 passed in the latest focused run; this is not live evidence.
      - [x] The migrated public-domain suite is clean: 40 tests plus 14
        subtests on suffix `6e74064714997f9d`. It now prepares typed candidate
        prerequisites before fanout, observes exact ready scopes, tests
        immutable supersession and repair, and rejects premature plan review.
        Runtime pagination/recovery fixture migration remains outstanding.
      - [ ] Full source checkpoint on suffix `6e74064714997f9d`: 621 tests
        and 258 subtests passed; 32 tests failed. Remaining failures are in
        old domain API, runtime-remediation and timeline fixtures plus the
        planner policy assertion. The run is not a passed local qualification
        gate and does not cover still-unimplemented checklist requirements.
      - [x] Source CLI/Desktop hook lifecycle fixtures use typed assignments,
        select only current ready nodes, and grow the contract through three
        genuine independent additions to exercise multi-page consumption.
        All seven persistent-source-transport cases passed on cache suffix
        `0065ac5d6359cf88`, including compaction reread and first terminal
        publication. This is real hook subprocess evidence, not native-host
        live qualification.
      - [x] Canonical outcome storage no longer duplicates acceptance into
        task-wide criteria or persists duplicate source-fragment text. The
        focused large-contract regression verifies exact derived provenance
        and complete replacement semantics on suffix `0065ac5d6359cf88`.
      - [x] Exact source roles now survive equal acceptance/verification text
        at creation, independent addition and complete replacement. Removed
        obsolete missing-reference guidance pointing at a handles envelope.
        Focused source, coverage, typed API, catalogue and hook checks passed:
        53 tests plus 116 subtests on suffix `d9a13c1faaed0ed5`.
      - [ ] Broad source checkpoint on suffix `e7b2778e0aad363c` was not clean:
        pytest reported 126 failed, 560 passed and 174 passed subtests. Failures
        include obsolete API/migration fixtures and a real discovery catalogue
        size regression. Keep qualification open while each group is migrated
        or fixed; do not restore compatibility to satisfy obsolete assertions.
      - [x] Removed repeated required-field lists and generic schema prose
        without changing fields, limits or semantic call preconditions. On
        suffix `e0274a2fcc605337` the complete catalogue is 60,847 bytes with
        4,689 bytes headroom, preserving both the 64-KiB limit and 4-KiB reserve.
        The all-20 first-call source-MCP scenario passed; subsequent broad
        source and native-host gates remain pending.
      - [x] Migrated the command/transport suite away from old assignments and
        publications. Explicit recovery reads precede fresh-connection commands;
        the test client correlates replies across catalogue notifications.
        Signed-loss reconciliation, copied-worker isolation and two-process
        admission pass: 35 tests and 113 subtests on `df0dd7f4c916ad9c`.
      - [x] The same prerequisite discipline now crosses persistent source MCP
        subprocesses: all 20 operations succeed with typed publication, separate
        documentation ownership, real semantic steering and fresh-connection
        recovery. First-call/schema and filesystem-policy tests passed five
        tests plus 33 subtests on suffix `df0dd7f4c916ad9c`. Host leases and
        artifact observations remain synthetic here; this is not native live.
      - [x] Replaced the obsolete outcome-assignment matrix with typed public
        scenarios: independently validated execution and explicit review,
        genuine pre-plan point steering, and unfinished-work recovery followed
        by requested chronology. Every successful call is validated against its
        advertised input schema; the observed operation set is exactly all 20.
        `tests/test_full_orchestration_matrix.py` passes on suffix
        `b68eaf4ba4f0ddc0`. Negative-case completeness and real-host qualification
        remain separate unfinished gates.
- [ ] All 22 profile first-call scenarios pass.
      - [x] The typed public-contract matrix covers every packaged profile:
        current-node selection, immutable assignment consumption, expected
        terminal kind and one first successful publication. It includes planner
        and writer profiles doing result-producing discovery. Removed the unused
        duplicate worker policy and fixed planner instructions to obey assigned
        node purpose. Together with worker-policy, projection-repair and closure
        tests, 80 tests passed on suffix `b68eaf4ba4f0ddc0`. This is local API/
        policy evidence, not execution of 22 native models or live qualification.
- [ ] Focused DAG and steering/resume tests pass.
      Source checkpoint: 52 graph-ledger, pure graph, model-routing, and
      source-stdio recovery tests passed on cache suffix `cc03341de00f4170`.
      This does not yet cover the integrated typed public protocol or hosts.
      Later source checkpoint: 75 tests passed on cache suffix
      `8b91856f78e3f749`, adding worker fingerprints, bounded stale-generation
      re-verification, all-kind projection repair, and exact outcome matching.
- [ ] Shortened real CLI E2E passes.
      - [ ] First native CLI attempt failed on suffix `dbd8f9d182710278`:
        scope incorrectly offered planning before baseline completion; two
        assignment attempts returned generic `ledger_error`. The baseline
        worker subsequently started, but no terminal publication was verified.
        Session `cortex-v12-smoke`, project `/tmp/cortex-typed-cli-e2e.MqwGLW`,
        was stopped with `./scripts/cortex-live-smoke stop --interrupt`.
        No clean exit marker or host qualification is claimed.
      - [x] Narrow availability regression: scope and bootstrap admission now
        share prerequisite evaluation; waiting bootstrap is unavailable and
        reports its unmet baseline. Delivery never offers bootstrap. Together
        with node-receipt and review-lineage checks, **20 tests passed** on
        suffix `ee2e2115eb1a1287`. Wire diagnostics and a fresh native run
        remain required before this gate can pass.
      - [x] Source stdio now verifies unavailable discovery/planning admission
        returns `assignment_not_ready` with the exact safe prerequisite reason,
        not `ledger_error`. The advertised scope explains bootstrap readiness;
        assignment descriptions rule out changing model/effort as a bypass.
        **58 tests and 113 subtests passed** on `9bb26073a5184cfa`, including
        all-operation source transport, review lineage and catalogue reserve.
      - [x] Updated the older bootstrap ledger scenario to reject premature
        discovery/planning without creating nodes, then admit each after its
        predecessor completes. The prior full run had **722 passed, 258
        subtests passed and one obsolete-sequence failure**; the corrected
        focused ledger/bootstrap set has **23 passed** on the same suffix.
        No runtime gate or test threshold was weakened.
      - [x] Full local recheck: **723 tests and 258 subtests passed** in
        151.09 seconds on suffix `9bb26073a5184cfa`. This is source evidence;
        short/full native CLI and Desktop gates are still separate.
      - [ ] Second short CLI attempt on `9bb26073a5184cfa` passed baseline
        routing and native first-read admission, but failed the worker's first
        result submission: required `unresolved` was omitted. Sanitized event
        sequence 12 recorded `validation_error`, field `$.unresolved`.
        Session `cortex-v12-smoke` in `/tmp/cortex-typed-cli-recheck.0p2Q1w`
        was stopped with `stop --interrupt`; no clean exit or completed E2E.
        Keep the required field and empty-array semantics; improve the live
        advertised description, not schema permissiveness or worker coaching.
      - [x] Required empty-list descriptions now explicitly cover completed
        discovery/baseline publications. Required fields, validation and empty
        array semantics are unchanged. A catalogue-reserve regression rejected
        the initial prose expansion (61,530 bytes, reserve 4,006); redundant
        operation prose was compressed instead of relaxing the 4,096 reserve.
        **53 tests and 212 subtests passed** on `325a412980c00f60`, including
        first-call schemas, all-profile source coverage and all-tool stdio.
      - [ ] Third short CLI attempt on `325a412980c00f60` again passed native
        bootstrap but failed first result submission: the check selector
        exceeded its bounded key length. Sanitized event 12 reported
        `validation_error` at `$.node_coverage[0].coverage[0].verification[0].check`.
        Session `cortex-v12-smoke`, project `/tmp/cortex-typed-cli-publication.XY95Yz`,
        was stopped with `stop --interrupt`. The assignment preserves check
        keys; clarify key versus description in the advertised property while
        retaining exact-key matching and the length bound. No live pass claimed.
      - [x] Consumed-assignment regression confirms check keys and descriptions
        remain distinct; schema rejects description prose as a bounded key.
        The advertised selector now names its exact source. **66 tests and
        212 subtests passed** on `e14d6a93344e03d7`; all input requirements,
        selector limits and canonical verification semantics remain unchanged.
      - [ ] Fourth short CLI attempt on `e14d6a93344e03d7`, project
        `/tmp/cortex-typed-cli-checkkeys.T9FUeS`, failed first publication on
        invalid check selectors; a subsequent corrected-selector attempt failed
        as incomplete. Stopped exact `cortex-v12-smoke` with `stop --interrupt`.
        Bounded sanitized transcript inspection confirmed complete assignment
        consumption, preserved `baseline` keys, worker fingerprint procedure
        execution, and omitted artifact observations in both submissions.
        No report content or raw transcript is copied into this document.
        Clarify existing conditional artifact requiredness in the live schema;
        no optionality, key restriction or evidence rule is relaxed.
      - [x] Conditional artifact requiredness is explicit in the advertised
        publication schema on `c610641d9ff3521a`. Focused recheck: **66 tests
        and 212 subtests passed**. A transaction regression additionally asserts
        omitted baseline observations cannot commit any database mutation;
        **725 tests and 258 subtests passed** in 150.98 seconds in the full
        suite, including that rollback regression, on the same suffix.
      - [ ] Fifth short CLI attempt on `c610641d9ff3521a` failed the first
        worker publication on an invalid check selector (sanitized event 12).
        Exact session `cortex-v12-smoke` in `/tmp/cortex-typed-cli-artifact.OyOS9Z`
        was stopped with `stop --interrupt`. No clean E2E claimed. Further
        live attempts pause for structural review of worker evidence: remove
        duplicate node scope and obsolete assigned/planning-item presentation,
        preserve complete scoped outcome context, and expose one unambiguous
        typed assignment authority. Keep strict publication validation.
      - [x] Worker evidence now has one typed assignment and separately scoped
        complete outcome context. Removed assigned/planning-item presentation,
        duplicated node JSON instructions, unused legacy renderer inputs and
        silent rework-to-partial text rewriting. First-read tests require one
        node scope, exact check keys, preserved constraints and no legacy aliases.
        Source checkpoint `fe58f4db382dc26e`: **173 tests and 127 subtests
        passed**, with one catalogue-reserve failure (43 bytes below the required
        reserve). Removed redundant publication-root prose, not any requirements;
        final validation of the refreshed suffix remains pending.
      - [x] Refreshed structural checkpoint `7ff58c80689c79b9`: **725 tests
        and 258 subtests passed** in 150.38 seconds, including catalogue reserve,
        isolated ownership, source requirement preservation and all-tool/profile
        source checks. README, security boundary and ledger page now describe
        the single typed worker authority. Native qualification remains open.
- [ ] Full all-tools/all-profiles CLI passes.
      - [x] `930184722fcb5fa6`: source release validation, packaged contract
        lint, marketplace validation, whitespace checks and read-only sync
        preview passed. Stable plugin/configuration were not modified. Updated
        architecture, gotchas, verification, storage and packaging pages remove
        obsolete initiative, schema-v1 and textual-scope instructions.
      - [x] `930184722fcb5fa6`: **752 tests and 280 subtests passed** in the
        full source suite (157.38 seconds). The subsequent root documentation
        refresh passed **68 focused transport/knowledge/policy tests**; package
        documentation validation and native qualification remain separate gates.
      - [x] `431da92bb9820301`: **70 focused tests and 121 subtests passed**,
        including the bootstrap-availability declaration, full-catalogue reserve,
        all required-property checklists and exact-line real tmux transport
        assertions. Root storage/README/security documentation now reflects
        typed schema v2 and bounded same-request projection repair.
      - [x] Root live transport now uses one literal insertion for every prompt
        length, a five-second drain and one named Enter, including the launcher.
        **51 transport/documentation tests passed** on unchanged payload
        `c0d2e9023a20659c`. Real tmux checks require an executed output line,
        not a marker echoed inside the command. Native TUI acceptance remains
        an independently observed live gate.
      - [ ] Eleventh short CLI on `139f0ff5d73e73e2`, project
        `/tmp/cortex-typed-cli-required.a7veWh`, failed before native dispatch:
        discovery was requested while baseline remained unsatisfied. The server
        rejected admission with `assignment_not_ready`; stopped the exact
        smoke session. This is not a qualified first-call flow.
      - [x] Schema-generated required-property descriptions on
        `139f0ff5d73e73e2`: **13 focused tests and 121 subtests**, then
        **738 tests and 280 subtests** in the full source suite (156.93 seconds).
        All 20 descriptions derive their checklist from the input schema;
        catalogue reserve and strict validation remain enforced. No live pass
        is implied by this source checkpoint.
      - [ ] Tenth short CLI on `a287026b3ec90094`, project
        `/tmp/cortex-typed-cli-scope.wDEEYP`, failed its first publication:
        required `unresolved` was omitted. Subsequent corrected requests also
        failed admission. The exact smoke session is stopped and absent.
        No retry qualifies as first-call conformance. Investigating native
        declaration clarity without relaxing required fields or evidence gates.
      - [x] Full source suite on `a287026b3ec90094`: **737 tests and 260
        subtests passed** in 152.49 seconds. Whitespace validation was clean.
        This verifies the revised scoped evidence and measured-interval paths;
        it does not certify a native CLI or Desktop run.
      - [x] `a287026b3ec90094`: **115 focused tests and 101 subtests passed**.
        Ordinary fingerprint intervals are returned directly as measured
        terminal metadata without invented boundary/reconciliation fields.
        Public errors retain allowlisted invariant reasons, never raw evidence.
        Bootstrap completion explicitly excludes downstream work; 21 ordinary
        profiles no longer expect the removed textual-scope input contract.
        Full suite and native qualification remain due for this checkpoint.
      - [ ] Ninth short CLI on `a58519a039a4464f`, project
        `/tmp/cortex-typed-cli-selectors.pArfXq`, passed check-key selection but
        failed first terminal evidence admission: completed status had an
        unresolved item, and ordinary artifact evidence included undeclared
        boundary metadata. Exact smoke session stopped. Safe invariant reasons
        are now retained at the public error boundary; assigned-scope status
        descriptions and procedure-generated ordinary observation intervals
        are being verified. No corrected retry is a live pass.
      - [x] Exact `check_key` selectors passed **138 focused tests and 101
        subtests**, then **733 tests and 260 subtests** in the full suite on
        `a58519a039a4464f` (151.46 seconds). Old `check` input is rejected at
        schema and transaction validation; remediation and views use only the
        canonical new field. Native first-call qualification remains open.
      - [ ] Eighth short CLI on `6877cc16749fde1d`, project
        `/tmp/cortex-typed-cli-sandbox.qmxngP`, confirmed both real worker
        fingerprint observations succeeded, but first publication failed when
        a check description was used as its selector. Stopped exact session;
        later retries do not qualify. Replaced ambiguous `check` with explicit
        `check_key` throughout schema, coverage, remediation and rendering;
        old-field rejection and refreshed regression checks remain required.
      - [x] Sandbox-compatible checkpoint `6877cc16749fde1d`: full source
        suite passed **732 tests and 260 subtests** in 151.50 seconds. Native
        CLI and Desktop remain unqualified; no source-to-live equivalence.
      - [ ] Seventh short CLI on `c107dfdd66d6cc73`, project
        `/tmp/cortex-typed-cli-explicit.jLkppD`, failed first publication with
        missing artifact observations and was stopped. Bounded diagnosis found
        an earlier procedure failure: workspace-write workers could not save
        manifests under the MCP-owned private ledger. No clean live claimed.
      - [x] Moved worker manifest scratch to owner-private system temp,
        isolated by user, Codex home and project. **36 focused tests passed** on
        `6877cc16749fde1d`, including executing the actual rendered before/after
        procedure through the real Linux Codex workspace sandbox with no model
        call, no permission expansion and no project/ledger manifest writes.
        This is sandbox/source evidence, not native E2E qualification.
      - [x] Explicit artifact contract and adapter parity passed **730 tests
        and 260 subtests** in 150.23 seconds on `c107dfdd66d6cc73`.
        Missing observations are rejected; explicit null is preserved, and
        baseline null cannot commit. The prior checkpoint's failures were
        obsolete required-field expectations, corrected without weakening
        validation. Release-readiness was rewritten for current typed storage
        and qualification; native requalification remains required.
      - [ ] Sixth short CLI attempt on `7ff58c80689c79b9` failed publication:
        baseline observations were omitted despite their runtime requirement.
        Exact session in `/tmp/cortex-typed-cli-authority.EQFWoA` was stopped.
        Corrective retries and an extra assignment read do not qualify as a pass.
        Publication now requires an explicit artifact observation or null for
        artifact-independent work; omission is no longer a supported form.
        Validation and native requalification remain pending.
- [ ] Full Desktop run passes on the unchanged CLI payload.
- [ ] Semantic version is unchanged; only the cache suffix is refreshed.
- [ ] Stable installed plugin remains untouched.
