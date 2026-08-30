# Official architecture synthesis

Status: review completed 2026-08-29; source/doc review only.  This document
checks the Cortex 1.12.1 target against the normative MCP protocol and the
official Codex extension boundaries.  It does not authorize removal of any
capability in [the parity contract](orchestration-feature-parity.md).

## Executive conclusion

The core split is correct: the host/model owns orchestration decisions and the
MCP server owns durable, atomic domain facts.  The architectural defect to
avoid is treating an MCP tool result as a host lifecycle callback.  MCP tools
are model-controlled server functions; they cannot wake a Codex worker,
inject a new user turn, or guarantee that a human answer reaches a native
Codex task.  The current `host_delivery` projection is therefore a durable
handoff record, not a delivery mechanism.  It must remain explicitly
best-effort until a supported host integration exists.

The official protocol does provide two related primitives, but neither should
be silently substituted for the current workflow:

* MCP elicitation is a server-to-client request for user input and is the
  standards-aligned future bridge for a server-originated question.  It is
  optional, must be capability-negotiated, must expose accept/decline/cancel,
  and must not request sensitive information.  The host must decide whether
  and how to render it.  See the [MCP elicitation specification](https://modelcontextprotocol.io/specification/2025-06-18/client/elicitation).
* MCP sampling is a server request for an LLM interaction through the client,
  not a worker-resume API.  The client retains model/prompt/user-control
  discretion.  It is unsuitable as a hidden continuation or approval path.
  See the [MCP client capability schema](https://modelcontextprotocol.io/specification/2025-06-18/schema).

Codex's documented plugin surfaces are packaging, skills, MCP connections,
and hooks; the public Codex MCP interface separately exposes thread/turn
operations and is explicitly experimental.  A plugin must not invent a hook
ABI or depend on an undocumented callback.  If a future host adapter is added,
it should target a documented Codex app-server/thread protocol or a host
implementation that explicitly supports MCP elicitation, with capability
negotiation and an end-to-end test.  See [Codex plugin packaging](https://developers.openai.com/plugins/build/plugins),
[Codex MCP interface](https://github.com/openai/codex/blob/main/codex-rs/docs/codex_mcp_interface.md),
and the [MCP lifecycle specification](https://modelcontextprotocol.io/specification/2025-03-26/basic/lifecycle).

## Layer decision matrix

| Capability/boundary | Officially compatible interpretation | Target disposition | Non-negotiable invariant |
| --- | --- | --- | --- |
| Explicit route activation and task anchor | Skills/hooks provide context and guardrails; the model invokes tools | **Retain/integrate** activation kernel | First project action is one model-issued task opening; a hook never creates it |
| MCP catalogue and argument contracts | `tools/list` advertises name, description, inputSchema and optional outputSchema; `tools/call` carries arguments | **Retain/integrate** registry-generated catalogue | The wire schema is authoritative; no parameter recipes in skills/prompts; every result matches its advertised output schema |
| MCP lifecycle/readiness | Client initializes, negotiates capabilities, then operates; requests need timeouts/cancellation | **Integrate** as transport prerequisite | No business mutation before initialized; bounded timeout and honest ambiguous-result reconciliation |
| Task locator/typed handles | Application-level capability/locator data carried in tool results | **Retain** | Exact server-issued value is copied; no reconstruction, truncation, or cross-task reuse |
| Command receipts/logical slots | Application-level idempotency layered on JSON-RPC; MCP itself does not define business replay | **Retain/integrate** in Domain Kernel | One logical mutation; identical replay returns the original fact; changed intent conflicts; ambiguous transport never opens a second binding |
| Clarification hold | Application state relating a question, answer, and assignment | **Retain**, with optional elicitation adapter | Hold is opened before the question; answer is recorded once; lack of host support remains visible and cannot claim continuation |
| User clarification rendering | Host-controlled UI; MCP elicitation is optional and capability-negotiated | **Integrate** as a future adapter; current coordinator flow remains fallback | Never send a question merely as prose and assume it is durable; never use sampling as a hidden answer path |
| `host_delivery` | Durable handoff receipt/projection, not MCP delivery | **Retain but rename/document as handoff** | Backend never schedules/wakes/fabricates a worker; delivery is acknowledged only by a supported host or exact worker publication |
| Same-worker continuation | Host/thread lifecycle concern, not a server-side MCP operation | **Retain policy; replace adapter when official host API exists** | Resume exact native task/assignment or report unavailable; no unrelated replacement and no silent new binding |
| Assignment and worker selection | Model/coordinator decision; server validates authorization and scope | **Retain/integrate** | Kernel validates; coordinator selects profile/model/DAG; server does not schedule autonomously |
| Worker event journal | Host/transport observability plus sanitized application events | **Retain/integrate** | Observation is non-authoritative and bounded; it records wire success/error once without raw prompts, args, reports, or secrets |
| Worker report/publication | Model-controlled tool call; server validates atomic publication | **Retain/integrate** | Worker owns its publication; incomplete payload is zero-write; duplicate successful mutation is a failure unless prior transport result was ambiguous |
| Evidence consumption/receipts | Application-level causal DAG and read audit | **Retain** | Declared predecessor evidence is consumed before publication; exact typed edge and scope are enforced by Kernel |
| Immutable plans and plan review | Application domain aggregate, not MCP protocol state | **Retain/integrate** | Review binds the exact immutable plan/view relation; approval/revision/cancel cannot select a mutable latest view |
| Governance/adaptation/initiatives | Model/coordinator judgment with durable evidence and deduplication | **Retain** | Advisory governance never becomes an autonomous scheduler or unsafe authorizer; initiatives use a material logical slot |
| Documentation impact and publication | Worker-owned project action plus application evidence | **Retain** | Material impact creates documentation work and independent verification; no-impact requires rationale |
| Verification/closure/final synthesis | Coordinator judgment over durable facts | **Retain** | Closure derives missing obligations and risks; backend does not declare success from a report reference alone |
| MCP Tasks utility | Protocol support for deferred tool results, not a workflow engine | **Defer/integrate only if needed** | Do not map assignment/clarification lifecycle onto MCP task handles; task polling/cancellation must be capability-negotiated and separate from business identity |
| MCP roots | Client-provided filesystem boundary | **Integrate where supported** | Worker/project scope must agree with host-provided roots; never infer authority from cwd alone |
| MCP logging/progress/cancellation | Protocol utilities | **Integrate selectively** | Use progress for observation and cancellation for bounded transport work; never treat progress as commit evidence |
| MCP authorization | Required for HTTP MCP; stdio remains local-process boundary | **Retain content-safety policy; add only for remote transport** | Token audience/PKCE/HTTPS rules apply when remote; never log credentials or pass tokens through |
| Codex plugin skills and hooks | Official packaging/lifecycle surfaces | **Retain/integrate** | Skills express semantics, not MCP call shapes; hook changes require trust review; hooks cannot fabricate unsupported lifecycle events |
| Codex app-server integration | Official but experimental Codex MCP interface with thread/turn operations | **Defer behind explicit adapter** | Pin/version-gate protocol, authenticate ownership, and prove exact thread continuation before enabling |
| Live-dev tmux | Local verification transport, not product orchestration | **Retain as test harness** | Ordinary Codex in exact isolated candidate; LLM reads pane/events and decides; helper only sends literal text/keys |

## Correct target sequence

The end-to-end sequence must preserve model ownership while making every
durable boundary server-owned:

```text
explicit Cortex route
  -> host/plugin activation context and negotiated MCP initialize
  -> model calls open_task once
  -> coordinator proposes DAG, governance depth, profiles and assignments
  -> Kernel validates assignment/evidence edges and receipts
  -> worker performs project work and publishes typed evidence
  -> if user input is needed:
       open clarification hold
       -> host renders question (elicitation when negotiated, otherwise coordinator UI)
       -> user accepts/declines/cancels
       -> record answer against exact hold
       -> supported host resumes exact native task
          OR durable handoff remains pending/unavailable for coordinator recovery
  -> planner publishes immutable plan
  -> host/user approves exact bound plan view
  -> implementation -> independent verification
  -> documentation impact -> documentation worker/verification when required
  -> governance/adaptation/rework decisions remain coordinator-owned
  -> closure aggregate checks all obligations and risks
  -> coordinator synthesizes final answer
```

The MCP protocol's normal flow is `initialize`/`initialized`, `tools/list`,
then `tools/call`; tool failures may be protocol errors or execution errors,
so the event journal must preserve both classes without pretending either is a
business replay.  The [MCP tools specification](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)
also makes output schemas optional and recommends serialized text alongside
structured content for compatibility.  Therefore Cortex should keep bounded
structured output for machine validation and a safe text rendering for hosts,
without moving parameter instructions into model prompts.

## Architectural changes required before claiming readiness

1. Treat the current `host_delivery` as a **handoff state machine** and expose
   its lack of host delivery as a first-class, testable capability result.
   It must never be described as a callback, resume, or scheduler.
2. Add an explicit capability matrix for the actual host session:
   `elicitation`, `roots`, `sampling`, progress/cancellation, and any Codex
   thread/turn adapter.  Unsupported capabilities must fail closed or use the
   documented fallback; they must not be inferred from a tool list.
3. Add wire-level tests for initialize/initialized, paginated tools/list,
   inputSchema/outputSchema agreement, protocol-error versus `isError` result,
   timeout/cancellation, and first-call open/record flows.  Unit façade tests
   are not sufficient.
4. Add a host-adapter contract test that proves: one answer, one exact hold,
   one exact native task continuation, and one first worker publication.  If
   the host lacks the adapter, the test must prove `unavailable` and the
   coordinator-owned recovery path instead of manufacturing success.
5. Keep the event journal observational.  A live verifier may read it, but no
   script may parse it to answer a question, approve a plan, retry a mutation,
   or accept the run.
6. Keep MCP Tasks, sampling, and elicitation behind separately negotiated
   adapters.  They are protocol capabilities, not replacements for the
   application aggregate, assignment lineage, user approval, or closure
   aggregate.
7. Qualify the exact isolated candidate after every cutover.  The source
   checkout, staged runtime, registered server, advertised catalogue, and
   active host must be identity-consistent before any live result is accepted.

## Migration rule

No current orchestration feature is deleted.  Features whose current owner is
the coordinator stay coordinator-owned; features that require durable
identity, authorization, legal transitions, replay/conflict handling, typed
evidence, or atomic publication move behind the Kernel/registry.  Any future
official host adapter is additive and must pass the parity matrix before it is
enabled.  If an official capability is unavailable in the current Codex host,
the result is a precise unsupported-capability state and an evidence-backed
coordinator recovery decision, never a guessed continuation.

## Host-adapter correlation design

The current observer must not claim a correlation that the official hook
payloads cannot prove.  The official Codex hook contract gives
`SubagentStart`/`SubagentStop` a parent session id, turn id, `agent_id`, and
`agent_type`; `SubagentStop` additionally has an optional transcript path,
`stop_hook_active`, and the last assistant message.  `PreToolUse` and
`PostToolUse` have a `tool_use_id`, tool name, and tool input/output.  The
transcript is explicitly not a stable hook interface, and the hook payload
contains native identifiers and potentially sensitive text.  These facts are
documented in the [official Hooks reference](https://learn.chatgpt.com/docs/hooks).

Consequently, a server digest over `(assignment_id, native_task_name)` cannot
be matched to a `SubagentStart.agent_id` by hashing alone: the server does not
know the agent id when it issues an assignment, and the start hook does not
receive the assignment ref or the coordinator's spawn input.  Hashing each
side independently is not correlation.  A new server-issued capability or a
documented host adapter is required.

### Typed adapter state machine

The future adapter should use this boundary, with all raw native values
remaining inside the host/observer process:

```text
Kernel: assignment authorized
  -> DispatchIntentIssued(assignment, opaque dispatch_capability, digest)
Host: accepted spawn action
  -> NativeStartObserved(agent_id, session/turn, H(agent_id), dispatch_capability)
Host: exact follow-up requested
  -> ContinuationAttempted(H(agent_id), dispatch_capability, attempt_id)
Host: follow-up accepted by the exact native task
  -> ContinuationAcknowledged(attempt_id)
Worker: first authorized publication
  -> PublicationAccepted(assignment, publication_receipt)
```

The host adapter, not the MCP server, owns the raw `agent_id`, native task
name, prompt, thread/task id, and transport response.  It sends the server
only a short-lived, opaque dispatch capability plus one-way fingerprints and
the bounded event kind.  The server binds the capability to the assignment in
one transaction and accepts only the expected state transition.  A capability
must be random, single-purpose, expiry-bounded, and unusable as an MCP tool
argument; it is not a substitute for a typed public handle.

There is no compatible bridge in the current package that can prove the full
chain.  `SubagentStart` alone proves that a native child was observed; it does
not prove which assignment caused it.  `SubagentStop` with a continuation
decision proves only that a stop hook requested another model turn; it does
not prove that Codex accepted the continuation, that it was the same process,
or that it published.  `PostToolUse` proves a particular tool call result, but
only when the host exposes the relevant call and the assignment relation has
already been bound.  The existing journal therefore correctly records these
events as observations and must remain unable to promote them into delivery
or acceptance by itself.

### Identity versus lineage

These are deliberately separate:

| Identity | Meaning | Evidence strength |
| --- | --- | --- |
| Assignment lineage | Durable Cortex work relation, parent/rework chain, scope, and publication slot | Server-authoritative |
| Native process identity | Host-local `agent_id`/thread/task identity and its lifecycle | Host-observed, fingerprinted |
| Dispatch correlation | Explicit host/server binding between the two | Requires the future capability adapter |
| Publication identity | Server-issued assignment publication receipt and logical slot | Server-authoritative |

The same native process may not be silently reused for a different assignment,
and a new process may not be called “the same worker” merely because it has the
same profile.  A same-worker claim requires the exact dispatch binding and a
successful worker publication under that same assignment.  Otherwise the
coordinator must treat the result as `unavailable` or choose an explicit,
parent-linked replacement after evidence-backed reconciliation.  Replacement
must retain lineage and evidence, but must issue a new assignment and never
reuse the old dispatch capability.

### Safe host outcomes

The adapter's only terminal delivery outcomes are:

* `acknowledged` — the host accepted the exact continuation attempt;
* `published` — the exact assignment produced its first accepted publication;
* `unavailable` — the host cannot prove exact acceptance, the child is gone,
  or the adapter is unsupported;
* `ambiguous` — transport ended without a proof of acceptance; this requires
  read-only reconciliation and must not mint a new binding automatically.

`SubagentStop`'s `stop_hook_active` must only prevent hook loops.  It is not a
delivery receipt.  A returned `decision: block`/continuation reason must not
be recorded as a successful resume.  Similarly, an event-journal row or final
report reference is not proof of a clean first publication if preceding hidden
tool failures or replays exist.

### Exact tests for the adapter seam

Before enabling a host adapter, add black-box tests that use the real hook
stdin/stdout contract and the real stdio MCP server:

1. Issue one assignment and verify one opaque dispatch capability is bound;
   replay returns the same binding and a changed assignment conflicts.
2. Feed a sanitized `SubagentStart` event and prove it remains only an
   observation unless the host supplies the matching capability through the
   adapter.  An unrelated `agent_id` must not correlate.
3. Feed matching start, exact-follow-up-attempt, and accepted-follow-up
   events; verify one state transition and byte-stable assignment lineage.
4. Feed `SubagentStop` with and without a continuation request; verify neither
   becomes delivery until host acceptance and worker publication are observed.
5. Publish once from the exact assignment; verify pending delivery reconciles
   once.  A second normal publication conflicts, and a different assignment
   cannot reconcile it.
6. Exercise timeout, process exit, restart, duplicate hooks, concurrent
   starts, unsupported host capability, and ambiguous transport.  Every case
   yields `unavailable`/`ambiguous` or an exact replay, never a replacement or
   duplicate mutation.
7. Assert the journal contains only bounded event kinds, fingerprints,
   assignment scope, and safe outcome metadata; it must contain no raw native
   identifiers, names, prompts, transcript paths, tool arguments, reports, or
   capabilities.

Until these tests can be run against a documented host adapter, the supported
production path is the existing coordinator-owned clarification hold plus
explicit `host_delivery` handoff and evidence-backed recovery.  This is a
deliberate limitation, not a reason to fabricate lifecycle guarantees.

## Second combined-source review

After the source-gate remediation, the full checkout suite was run without
refreshing a candidate, opening live-dev, or touching the stable profile:
**234 passed, 141 subtests passed, 25 failed**.  There is no P0/P1 clearance.

The first failure class is a reproducibility defect in the source qualification
boundary.  Candidate/provenance and observation-lease tests attempt to build a
candidate after an earlier test has created
`plugins/cortex/scripts/cortex_runtime/__pycache__`; the exact runtime payload
manifest then correctly rejects the unexpected directory.  The cleanup is
order-dependent, so later tests never reach their intended symlink, lease, or
candidate assertions.  The source suite must isolate or clean generated
bytecode before each build while retaining the manifest's fail-closed behavior.

The second failure is a real production interface mismatch: `v12_service` calls
`V12Store.human_view(..., require_fresh=False)`, but the store method currently
accepts no `require_fresh` keyword.  This breaks final plan publication approval
view generation and must be fixed at the source API boundary before any live
qualification.  It is unrelated to the host marker and cannot be dismissed as
an environment issue.

The remaining semantic-registry failure still expects all operation names in
the lean control `SKILL.md`, while the canonical catalogue is in the bounded
post-anchor reference.  The test/gate must read the same authoritative
progressive-disclosure surface; putting the catalogue or MCP parameter recipes
back into the activation kernel would violate the architecture.

The v20 review remains unchanged: the marker is integrity evidence only.  It
is embedded in the trusted worker brief and fingerprinted by the journal, but
the official `SubagentStart` payload does not include that brief or assignment
relation.  No assignment-to-native-worker correlation, resume, or callback may
be claimed until an official host adapter binds the marker at the spawn
boundary and authenticates the one-time transition.  `SubagentStart` text,
MCP echo, `SubagentStop`, `stop_hook_active`, and final publication alone never
prove that link.

### Source implementation boundary — v20

`v20-dispatch-correlation-marker` implements only the durable server half of
this design. Each newly authorized assignment receives one random stable
`dc_` observation marker and a server-stored one-way fingerprint. The trusted
worker renderer embeds the marker unchanged in the original dispatch brief;
the public assignment projection and a later clarification handoff expose the
same marker plus fingerprint. It is deliberately non-authorizing: it is not a
public MCP input, continuation capability, task handle, or proof that a host
started/resumed a process. The event journal records only an additional
one-way fingerprint, never the marker or raw native identity.

Assignment replay returns the already persisted marker. Cross-task or
cross-assignment lineage remains validated by the canonical ledger, and
publication reconciliation still proves assignment lineage only. A legacy
assignment without a pre-spawn marker is explicitly uncorrelated; the server
does not reconstruct one after the fact. Actual host `started`,
`continuation_attempted`, `acknowledged`, `unavailable`, or `ambiguous`
transitions remain future adapter work and are not claimed by this migration.

The v20 marker is therefore **marker integrity**, not native lifecycle
correlation.  The official `SubagentStart` payload does not carry the worker
brief or the assignment relation, and the current lifecycle observer does not
receive the marker from that event.  Independently hashing the server marker
and the hook's native `agent_id` is not correlation.  The marker becomes a
valid bridge only when a documented host adapter receives the exact marker at
the spawn boundary, binds it to a one-way `agent_id` fingerprint, and submits
that binding through a private authenticated seam.  A worker echoing the
marker in model text or a later MCP call is insufficient because model text is
untrusted and may be omitted or altered.  Do not add the marker to public MCP
arguments solely to make this correlation possible.

### Ownership of later lifecycle stages

Compaction preserves the durable assignment/hold/plan/publication snapshot and
the exact server-issued handles; it does not recreate a native process or
claim a continuation.  Plan approval is a host/user decision against the
immutable view relation captured at review-open time.  Documentation-impact
assessment remains coordinator policy and documentation publication remains
worker-owned.  Governance/adaptation/rework remain model decisions validated
by the Kernel.  Closure is a read-only derivation over all required approvals,
assignments, evidence, verification, documentation outcome, unresolved risks,
and pending host handoffs.  None of these stages may infer completion from
`SubagentStop`, an event-journal observation, or a transport-level success.

## Combined source review result

The broad source suite was run against the current checkout only (no
candidate, live session, or stable profile): **138 passed, 124 subtests
passed, 16 failed**.  The failures are release blockers for source
qualification:

* Six `test_worker_handoff_contract.py` failures show that the lean activation
  skills no longer contain assertions moved into the post-anchor references.
  The fix should make the source gate inspect the authoritative progressive
  disclosure references, or restore only semantic anchors to the lean kernel;
  it must not restore MCP argument recipes.
* One semantic-registry failure has the same cause: it expects the public
  catalogue in the lean control skill although the catalogue is now in the
  post-anchor reference.
* Nine sync/marketplace/candidate failures stop at earlier catalogue checks
  (`model-routing` generated markers or `Public semantic catalog`) before
  reaching the filesystem/provenance assertion under test.  Candidate refresh
  and symlink gates therefore cannot yet be trusted because the intended
  assertion is masked.

No P0/P1 clearance is possible until these source-gate mismatches are fixed
and the suite is rerun cleanly.  This review made no production or test edits.

The official Hooks reference also says matching hooks may run concurrently and
that some specialized tool paths may opt out of the default hook path.  Hook
observations must consequently remain non-authoritative: absence of an Agent
Pre/PostToolUse event is not proof that a subagent did not start, while
presence of `SubagentStop` is not proof of continuation.  Only an explicit
host binding plus the first assignment-owned publication can close that
correlation.

## Third combined-source release gate

After the source fixes, the broad checkout suite was rerun from a bytecode-free
environment with `PYTHONDONTWRITEBYTECODE=1`:

- **261 tests passed**
- **141 subtests passed**
- **0 failures, 0 errors, 0 skips**

The targeted provenance, observation-lease, candidate-builder, publication,
approval-view, clarification-hold, lifecycle-hook, renderer, registry,
activation, and development-artifact-boundary checks all reached their
intended assertions.  `render_cortex_tool_catalog.py --check` and the package
prompt contract lint also passed, with no Python bytecode left in the bundled
plugin tree.

The strict manifest still rejects deliberately injected bytecode; the clean
test environment prevents unrelated earlier tests from masking that intended
assertion.  `V12Store.human_view` now accepts the freshness option used by the
approval-view path, and the immutable plan/view relation is exercised through
the domain/publication tests.

This is **source-gate clearance only**.  Candidate refresh/qualification and
the real attached live-dev multi-turn run remain unrun in this review.  Native
assignment-to-worker correlation remains explicitly unavailable until a
documented host adapter supplies and authenticates the server marker at the
official spawn boundary.  No live resume or callback claim is made.

## Latest post-live root-fix review

The current source was independently checked against the three diagnostic
failure classes that motivated the latest changes, at both the public/domain
boundary and the real stdio MCP dispatch path:

1. Qualified `open_task`/bootstrap activation is classified through the
   advertised public contract rather than through a prose acknowledgement.
2. An incomplete planner `publish_plan` evidence envelope is rejected before
   mutation; a complete first planner publication succeeds.  The envelope is
   closed and role-complete, and the same canonical evidence relation is used
   for plan, result, and documentation publications.
3. `dispatch_brief` now returns separate typed opaque `task_ref`,
   `delegation_ref`, and `assignment_ref` values, plus a server-derived
   `publication_next_action`.  `read_task` accepts only the task handle; using
   an assignment handle as `task_ref` fails with a validation error instead of
   silently reading the wrong entity.

The focused stdio/public-contract gate passed **32 tests and 18 subtests**,
including the first-call/bootstrap, complete/incomplete publication,
distinct-handle, wrong-relation, worker handoff, and strict development
artifact-boundary assertions.  The complete source gate remains **261 tests
passed, 141 subtests passed, 0 failures, 0 errors, 0 skips**.  The generated
catalog check and contract prompt lint also passed.  A bounded repository
search found no production or test reference to `docs/architecture`; the
architecture material therefore remains development-only and cannot become a
runtime dependency.

There is no compatibility shim or reduced-completeness acceptance in this
result: invalid evidence fails the advertised schema/validation path before a
store mutation, while complete evidence follows the canonical publication
path.  MCP argument names, shapes, and call recipes remain absent from skills,
prompts, and worker instructions; they are defined by the advertised tool
schemas/descriptions only.

This is still source clearance, not live clearance.  Candidate refresh and
live-dev were intentionally not run under this review.  The architecture
remains honest about native dispatch: official Codex hook payloads do not by
themselves carry the server assignment relation, so the v20 marker can prove
integrity only.  Until a host adapter injects and authenticates that marker at
the official spawn boundary and observes sufficient hook evidence, the
assignment-to-native-worker relation is `unavailable`/`ambiguous`; no process
resume, callback, or correlation guarantee is claimed.

## Post-live worker-bootstrap and prompt audit

The latest production renderer and dispatch contract were reviewed directly.
The trusted worker policy requires the fresh worker's first semantic action to
consume the server-owned assignment evidence using the opaque assignment
anchor supplied by the active contract.  It explicitly forbids task reads,
project inspection, or project work before that consumption succeeds.  The
consumption result supplies the typed continuation evidence; the worker then
publishes through the applicable semantic publication operation.  The
coordinator receives the exact server-rendered `rendered_message` from the
dispatch projection and does not reconstruct a host prompt or translate the
typed anchors into another request.

The generated-worker and continuation-message audit passed.  It found no MCP
property names, request shapes, field inventories, limits, enums, or example
payloads in generated worker/continuation instructions.  Tool operation names
needed to identify semantic actions remain descriptive policy terms; callable
argument contracts remain exclusively in the advertised schemas and property
descriptions.  The prompt lint passed against both packaged profiles and
generated messages.

The focused regression gate passed **25 tests and 11 subtests**, covering the
prohibited assignment-as-task first read, typed bootstrap anchors, complete
first publication, renderer byte/identity constraints, continuation leakage,
and development-only artifact boundary.  The full source gate passed **261
tests and 141 subtests**, with **0 failures, 0 errors, and 0 skips**.  The
generated catalog/package check passed, contract lint passed, and the bundled
plugin contains no Python bytecode.  No candidate refresh, live session, or
stable-profile operation was performed.

This provides source-level P0/P1 clearance for the bootstrap fix and prompt
audit.  It does not promote the native-dispatch correlation claim: as required
by the official Codex hook semantics, marker presence in a rendered message
or an MCP echo is not proof that the native host started that exact worker.
That relation remains unavailable/ambiguous until the authenticated official
spawn-boundary adapter exists; no native resume or callback is inferred.

## Publication simplification and observability gate

The publication input contract now has one closed, uniform evidence-fact
shape rather than branch-specific alternatives: each fact has a state and a
complete summary.  Plan publications additionally require contiguous ordered
stages, exact contract coverage, documentation impact, and the complete common
evidence arrays; result publications retain the same common envelope and add
outcome/changes.  The server-side admission path rejects incomplete evidence
before mutation, and command receipts preserve replay idempotency.  The
public schemas therefore do not weaken completeness to accommodate an old
fixture or a second first-call shape.

The sanitized journal review confirms that production MCP call sites provide
only bounded semantic fault codes (for example `validation_error` or
`report_incomplete`), not exception text, request values, prompts, or payload
fragments.  Structural validation observations are limited to an allow-listed
JSON location, field name, expected category, and corrective semantic action.
Anchors and dispatch markers are one-way fingerprints.  The journal remains
best-effort, owner-only, bounded, observational data and cannot alter a tool
result or authorize a retry.

The focused journal/publication gate passed **33 tests and 13 subtests**.
Catalog generation, package contract lint, bytecode absence, and the strict
architecture artifact boundary also passed.  A fresh full source run exposed
**3 failures, 260 passes, and 141 subtests passed**: the three parametrized
plan-review tests still construct the former evidence-fact shape using
`state` plus `reason`, while the current advertised closed schema correctly
requires `state` plus `summary`.  This is one source-gate blocker, not three
independent runtime defects; the tests must be migrated to the current
canonical envelope before P0/P1 clearance can be granted.  No production or
test files were changed in this review.

The remaining historical-store checks are forward readability/migration of
already persisted records and private projection migration, not advertised
legacy MCP operations: the public catalogue contains only current semantic
operations, and new writes cannot create the historical one-chunk form.
Nevertheless, any future removal of historical readability must be an
explicit migration decision; it must not be disguised as a compatibility API.

Candidate refresh, live-dev, and stable-profile operations were not run.

## Final post-cleanup source gate

After the canonical fixture cleanup, the independent full source gate was
rerun against the current checkout.  It passed **263 tests and 141 subtests**
with **0 failures, 0 errors, and 0 skips**.  The prior three failures were
confirmed as stale `reason`-shaped evidence fixtures; the current positive
fixtures now use the uniform closed `state`/`summary` evidence fact, while
negative invalid-shape tests continue to exercise pre-mutation rejection.

The focused publication, journal, public-stdio, and development-boundary gate
passed **30 tests and 13 subtests**.  The generated catalogue check passed,
contract prompt lint passed, marketplace/package validation passed, and the
bundled plugin contained no Python bytecode.  The strict development artifact
boundary remains intact, including rejection of deliberately injected
bytecode.  No public legacy operation or compatibility alias was introduced;
historical readability remains limited to forward reading/migration of already
stored records and private projection internals.

The sanitized observability assertions still distinguish `validation_error`
from `report_incomplete` and expose only allow-listed structural metadata;
they never expose request values, raw exception text, prompts, or payloads.
Candidate refresh, live-dev, and stable-profile operations were not run.
This final source gate has zero P0/P1 blockers; native dispatch correlation
remains honestly unavailable/ambiguous pending an authenticated official host
spawn-boundary adapter.

## Final open-task contract gate

The current advertised `open_task` contract is coherent and closed: it
requires one top-level `task` object; canonical task identity, objective,
original request, language, constraints, context, and a non-empty `outcomes`
list are nested under that object; every outcome pairs one requirement with a
non-empty acceptance list.  The domain adapter preserves the original request
and language, derives durable requirements/acceptance and verification data
deterministically, and does not invent additional user intent.  Old flat
arguments such as top-level `project_root`, `requirements`, or
`acceptance_criteria` are rejected at the public stdio schema boundary, with
no runtime alias.  Forward-only stored-state readability remains separate
from public compatibility.

The focused natural-first-call and negative omission gate passed **9 tests and
7 subtests**, and all 15 advertised tool schemas remain closed with typed
output schemas.  Catalog generation, prompt/property lint, and marketplace
package validation passed; the privacy-bounded journal and development-only
artifact checks remained green, with no plugin bytecode.

The final broad suite was **not clean** in this checkout: **253 tests passed,
11 failed, and 141 subtests passed**.  All 11 failures are downstream
qualification scenarios still sending the retired flat `open_task` shape;
the real stdio server correctly returns a validation error at `$.task` before
mutation.  This is one remaining fixture migration blocker, not grounds for
weakening the coherent contract or adding a compatibility shim.  The required
zero-failure/P0/P1 clearance therefore cannot be granted until those tests use
the canonical nested task object.  No production or test files were changed by
this review, and candidate/live/stable operations were not run.

## Final open-task migration gate

After the remaining Phase D fixtures were migrated to the canonical nested
task payload, the independent full source gate passed **264 tests and 141
subtests**, with **0 failures, 0 errors, and 0 skips**.  The focused natural
stdio/open-task and old-shape rejection checks passed **17 tests and 13
subtests**.  The old flat payload remains rejected before mutation; the
canonical nested payload opens one task and returns its typed task handle.

Catalog generation, contract prompt/property lint, marketplace/package
validation, and the strict development-only artifact boundary all passed.
The bundled plugin contains no Python bytecode.  No production compatibility
alias was introduced; the migration changed qualification fixtures to match
the already-advertised contract.  Candidate refresh, live-dev, and
stable-profile operations were not run.

This is final source-gate clearance with zero P0/P1 blockers.  The separate
native dispatch correlation limitation remains intentionally honest and
unchanged: without an authenticated official spawn-boundary adapter it is
`unavailable`/`ambiguous`, and no resume or callback guarantee is inferred.

## Final assignment-mission and 15-tool first-call audit

The assignment route remains server-owned and coherent: the server resolves
the effective task contract, model/profile routing remains sourced from the
packaged profile table, the dispatch brief carries distinct typed anchors and
the exact rendered worker message, and bootstrap/publication semantics remain
unchanged.  No old assignment alias is present in the advertised catalogue;
stored rows remain forward-readable without becoming a legacy public API.

`record_steering` is not a first-call P1 blocker.  Its nested
`steering_delta` is a closed semantic object with two independently meaningful
branches: retiring existing contract items and/or adding new items.  The
allow-listed `anyOf` requires at least one branch while permitting both in one
atomic steering transaction.  The nested additions are themselves closed and
typed, and the binding plus optional supersession relation remain explicit and
server-resolved.  This is not an ambiguous parallel copy of the same semantic
field, and tests cover first call, replay, supersession, malformed shape, and
effective-revision behavior.  A complete steering call succeeds; missing both
branches fails before mutation.

The focused natural-stdio, old-shape, steering, publication, journal, and
boundary gate passed **17 tests and 13 subtests**.  The full source gate passed
**264 tests and 141 subtests**, with **0 failures, 0 errors, and 0 skips**.
Catalog, prompt/property lint, marketplace/package validation, development
artifact boundary, and bytecode checks all passed.  No production compatibility
alias was introduced.  Risk classification: no P0/P1 issues remain; the
native host correlation limitation described above remains a known architectural
P2/unavailable capability, not a first-call contract defect.  Candidate,
live-dev, and stable-profile operations were not run.

## Packaged-skill pre-anchor Read audit

The packaged activation hook allows the generic host `Read` exception only
when the event is exactly `Read` with one absolute `file_path`, and the path
resolves to a regular file below the active installed `PLUGIN_ROOT/skills`
tree.  It rejects relative paths, explicit traversal components, symlinks,
outside-project files, malformed input, and non-Read lookalikes.  Project,
repository, shell, and worker actions remain blocked before the task anchor;
the semantic task-opening call and bounded bootstrap resource reads remain
allowed.  Selection state is keyed by a hashed turn and contains no prompt,
file content, tool arguments, or output values.

The black-box hook gate passed **13 tests**, and the final full source gate
passed **266 tests and 141 subtests**, with **0 failures, 0 errors, and 0
skips**.  Catalog generation, prompt lint, marketplace/package validation,
development-boundary checks, and plugin bytecode checks passed.

Two architectural P1 issues remain despite the green existing tests and must
not be waived: (1) state is looked up only by the current `turn_id`, so an
unrecognized or mismatched turn does not fail closed against a previously
selected route; and (2) the hook validates the path by `resolve()` and then
returns an allow decision without opening or holding the file, leaving a
theoretical filesystem replacement race between validation and the host's
actual Read.  A hardened implementation needs a session/turn binding that
fails closed on mismatch and an OS-level no-follow/open validation seam (or a
host guarantee that the authorized file identity is the one actually read).
Until those are addressed, this is test-suite clearance but not P0/P1
architecture clearance.  No candidate/live/stable operation was run.

## Pre-anchor skill activation viability review

The packaged `openai.yaml` declares the MCP dependency and disables implicit
invocation; explicit `$cortex:orchestrator` selection supplies the activation
skill context through the host.  The orchestrator kernel contains the
pre-anchor semantic contract, while its post-anchor reference is loaded only
after the task anchor.  A clean first model action can therefore be `open_task`
without a generic filesystem `Read`; MCP resource/bootstrap operations remain
qualified alternatives rather than a required model file-read step.

The current hook still contains `_is_packaged_skill_read` and allows a generic
`Read` of a resolved regular file below `PLUGIN_ROOT/skills` before anchoring.
Existing black-box tests pass (**13 tests**), and the full source suite passes
(**266 tests, 141 subtests, 0 failures, 0 errors, 0 skips**), with catalog,
prompt lint, marketplace/package, boundary, and bytecode checks passing.  The
tests demonstrate containment and turn-mismatch handling but do not make this
generic filesystem exception architecturally necessary or TOCTOU-safe.

This remains a P1 blocker for the requested strict architecture: remove the
generic `Read` pre-anchor allowance and rely on host-supplied skill context or
an official qualified MCP resource/bootstrap route.  If a host can still emit
a legitimate generic `Read` before `open_task`, the integration is not a
viable clean run and must fail closed rather than broaden the exception.  The
hook's deny output is sanitized (tool-class reason only; no path, arguments, or
values).  No production/test changes were made in this review; candidate,
live-dev, and stable-profile operations were not run.

## Settled activation viability gate

The settled shared tree removes `_is_packaged_skill_read` and every generic
filesystem `Read` allow branch from the activation hook.  AST/source inspection
shows the pre-anchor allow set now contains only the semantic task-opening
operation and qualified resource/bootstrap tool names.  A black-box `Read` of
an installed skill is denied, as are project/repository/shell/worker classes;
the denial contains only a sanitized tool-class reason.  Session-scoped turn
mismatch handling fails closed, while normal follow-up turns and lifecycle
events retain their intended behavior.

The activation black-box gate passed **10 tests**.  The full source gate passed
**268 tests and 141 subtests**, with **0 failures, 0 errors, and 0 skips**.
Catalog generation, contract prompt lint, marketplace/package validation,
development-boundary validation, and plugin bytecode checks all passed.

This closes the previous pre-anchor generic-Read P1 blocker and provides
source-level P0/P1 clearance.  Explicit skill activation can now have a clean
first model tool of `open_task`; post-anchor references are loaded only after
that anchor, and no model-issued filesystem read is required.  Candidate,
live-dev, and stable-profile operations were not run.

## Practical packaged-skill Read boundary review

The current hook implements the requested less-strict trusted-plugin boundary:
it accepts exactly one absolute `file_path`, requires a private candidate
receipt, binds `PLUGIN_ROOT` to the active package, rejects traversal and
symlinks, requires a regular file below `skills`, and emits only sanitized
tool-class denial reasons.  Session/turn state is scoped and mismatches fail
closed.  The candidate launcher exports the verified candidate path, build,
source digest, and receipt into the ordinary Codex environment.

The black-box boundary suite passed **47 tests** and the full source suite
passed **268 tests and 141 subtests**, with **0 failures, 0 errors, and 0
skips**.  Catalog generation, prompt lint, marketplace/package validation,
development-boundary checks, and bytecode checks passed.  No same-user TOCTOU
claim is made; the boundary is documented as trust validation before the host
opens the file.

One implementation P1 blocker remains for actual live use: `CORTEX_SKILL_DIGESTS`
is optional in `_trusted_packaged_skill_read`, and `scripts/cortex-dev` does
not export it.  Without that map, a verified candidate receipt authenticates
the package identity but does not bind the current bytes of the specifically
requested skill file.  A changed file beneath the candidate path could
therefore pass the hook's path/receipt checks.  The live path must either
export a digest map derived from the verified candidate manifest and require
the exact per-file digest, or make the receipt itself provide an equivalent
file-content binding.  Until that is implemented and exercised in the actual
launcher path, source tests are green but the requested live Read trust claim
is not fully qualified.  Candidate, live-dev, and stable-profile operations
were not run.

## Final launcher-to-hook skill-digest review

The launcher now exports `CORTEX_SKILL_DIGESTS` and the hook validates the
requested skill's exact file digest when the map is present, with a build ID,
receipt, candidate root, regular-file, size/path, and hash binding.  The
receipt and candidate tree checks reject symlinks, traversal, extra payload,
and foreign paths; denial output remains sanitized.  The accepted same-user
TOCTOU limitation is documented and no atomic file-open claim is made.

The implementation still has a P1 provenance gap: the launcher constructs the
digest map by enumerating the mutable candidate `skills` directory after the
receipt verification, rather than reading the exact already-verified candidate
manifest records.  The map also lacks an explicit bounded key/count/size
validation envelope before export.  A post-verification mutation could thus
be reflected by the map itself.  The fix must derive the map from the verified
manifest (and require exact skill-relative keys, bounded count/length, and
sha256 values), then bind the envelope to the receipt build/candidate digest.

The focused hook/live boundary checks passed **47 tests** before this review;
the current full-source attempt was contaminated by a generated
`plugins/cortex/hooks/__pycache__` artifact from the black-box subprocess and
therefore cannot be counted as a clean release run.  The last clean broad
source result remains **268 tests and 141 subtests, 0 failures/errors/skips**;
catalog, prompt lint, package validation, and bytecode checks were green before
the artifact was generated.  This review therefore does not grant zero-P0/P1
clearance.  Candidate, live-dev, and stable-profile operations were not run.

## Final manifest-authorized skill digest review

The receipt now records bounded per-skill paths and SHA-256 values, and the
hook requires an envelope bound to `build_id` and `candidate_digest`, validates
strict key/hash shape and limits, and compares the current requested bytes.
The launcher no longer enumerates the candidate directory when exporting the
map; it reads the receipt's `skill_records`.  Candidate identity itself is
content-addressed and parity-verified before the receipt is produced.  The
accepted same-user TOCTOU limitation remains explicitly documented rather
than presented as atomic authorization.

The source/black-box gates passed: **268 tests and 141 subtests**, with **0
failures, 0 errors, and 0 skips**; activation/receipt focused checks passed,
catalog generation passed, prompt lint passed, marketplace/package validation
passed, the development boundary passed, and no plugin bytecode was present.

One implementation P1 remains: `cortex-dev` reads and parses the receipt file
directly to build the exported envelope instead of calling the canonical
verified-receipt reader at that final boundary.  The preceding candidate
verification and owner-only receipt controls reduce the risk, and the hook
still checks current bytes, but a receipt mutation between verification and
launcher export is not independently rejected as a receipt-integrity failure.
The launcher must consume the canonical verified receipt records (including
receipt digest, candidate/build relation, strict record bounds, and exact
candidate path) before exporting.  Until that call path is made explicit and
tested, this is not zero-P0/P1 clearance.  Candidate, live-dev, and
stable-profile operations were not run.

## Final manifest-authorized receipt gate

The settled launcher-to-hook path now consumes the already verified receipt
identity in memory and does not reopen the receipt file.  The receipt reader
uses one owner/private/regular-file `O_NOFOLLOW` read, validates strict JSON
schema and bounds, and returns the same validated object used for launcher
output.  The receipt contains the bounded skill records produced within the
candidate parity transaction; the launcher projects those records directly
into the digest envelope and adds the exact verified build and candidate
digest.  No launcher directory enumeration is an authority source.

The hook requires matching build and candidate digests, validates every skill
key/hash and envelope bound, resolves only the active candidate's skills tree,
and compares current file bytes.  Sanitized denial behavior and the accepted
same-user TOCTOU limitation remain unchanged and make no atomic authorization
claim.

The final full source suite passed **268 tests and 141 subtests**, with **0
failures, 0 errors, and 0 skips**.  Focused launcher/receipt/hook/boundary
checks passed **10 tests**; catalog generation, contract lint, marketplace
validation, and bytecode checks all passed.  This provides source-level
clearance with zero implementation P0/P1 blockers aside from the explicitly
accepted same-user TOCTOU trust limitation.  Candidate, live-dev, and
stable-profile operations were not run.

## Final launcher traceback and receipt gate

The settled launcher uses `set -euo pipefail`; receipt verification and the
digest-envelope projection fail cleanly before `exec codex`.  A valid verified
receipt supplies the envelope and proceeds, while an invalid receipt or
malformed skill manifest exits nonzero without a traceback, partial Codex
launch, or partially exported digest state.  The launcher consumes the
already-verified in-memory receipt output and does not reopen the receipt.

The final source suite passed **268 tests and 141 subtests**, with **0
failures, 0 errors, and 0 skips**.  Catalog generation, prompt lint,
marketplace/package validation, launcher/activation black-box checks,
development-artifact boundary, and no-bytecode checks passed.  No conditional
raise anti-pattern was found in the launcher failure path; controlled
`SystemExit` validation failures occur only before launch and are converted by
the strict shell guard into a clean nonzero exit.

Source-level P0/P1 clearance is complete, with only the explicitly accepted
same-user TOCTOU trust limitation remaining.  Candidate refresh, live-dev,
and stable-profile operations were not run.

## Native worker startup diagnosis (official-doc evidence)

The observed sequence `SubagentStart` -> worker MCP `server_ready` -> no worker
semantic call -> `SubagentStop` with unknown status is not evidence that the
worker completed, failed, or resumed.  Official Codex hook payloads expose
native startup/stop observations and Agent tool input/output separately, but
do not promise that `SubagentStart` contains the dispatched message, that MCP
server initialization implies model execution, or that `SubagentStop` proves
continuation.  A server-ready event proves only that the worker process
connected and initialized its MCP server.  The absence of a semantic call is
therefore an honest `no_first_action_observed`/delivery-unknown state.

The local implementation correctly keeps lifecycle events observational and
does not bind a native identity from a marker echo.  The practical failure is
at the host delivery seam: startup and MCP initialization can succeed even
when the exact initial worker message was not accepted by the native Agent
boundary, was delivered to a worker that never produced a turn, or was stopped
before its first semantic action.  No amount of MCP-side retry can distinguish
those causes.

Minimal architectural correction: make the host adapter return an explicit
dispatch-attempt receipt only after it submits the exact server-rendered
message to the official Agent boundary, then require the first worker semantic
action (assignment-evidence consumption) as the only durable acceptance
receipt.  Bound the wait; on timeout or `SubagentStop` without that action,
classify the assignment as delivery-unknown/unavailable and reconcile
read-only.  Do not infer completion, same-worker continuation, or create a new
assignment automatically.  Keep the MCP server-ready event as diagnostic
evidence only.  This preserves the existing orchestration features while
making the missing first worker action visible and actionable.

Official references used for this conclusion are the Codex Hooks payload and
decision semantics and the Codex MCP interface documented in the references
below.  No production, candidate, stable, or live state was changed.

## Native subagent skill/context audit

Official OpenAI material documents skills as explicit environment/context
inputs and MCP tools as tools available to the model; it does not document an
in-process guarantee that a user-selected skill, plugin activation state, or
MCP route is inherited by every native subagent.  Likewise, the official
Responses interface treats `instructions` as a system/developer-context input
and tool calls as separately supplied capabilities.  A server-rendered worker
message delivered through the native Agent spawn is therefore valid task
context, but it is not equivalent to a new user route selection and cannot
itself grant a child access to a missing skill/plugin/MCP registration.

Applied to the reported `could not access the active Cortex route` failure,
the likely boundary defect is child-context provisioning, not the ledger:
the child started with a process and possibly an MCP connection, but its
effective skill/plugin route was absent or not exposed at the time the model
had to choose its first semantic action.  Startup and `server_ready` are not
proof that the child inherited the coordinator's route or role instructions.

Minimal supported design: make the native Agent spawn an explicit host
boundary that supplies the child role as trusted developer/instructions
context and exposes the already registered Cortex MCP server in that child
environment.  The coordinator passes the exact server-rendered assignment
message as task context; the child does not re-activate the user route, fetch
skill files, or reconstruct MCP arguments.  The first child action remains
assignment-evidence consumption, whose result is the durable proof that role
context and MCP access were actually usable.  If the host cannot provide this
child context, return `route_unavailable` before dispatch and do not pretend
that an MCP `server_ready` event is a successful worker start.

This is a provisioning contract, not a recovery loop: no automatic second
route activation, generic filesystem read, new assignment, or fabricated
continuation is supported.  The conclusion is grounded in the official
[Codex response instructions/tools model](https://developers.openai.com/api/reference/cli/resources/responses/methods/create),
the official [Codex skill resource model](https://developers.openai.com/api/reference/go/resources/skills),
and the official [Codex Hooks reference](https://learn.chatgpt.com/docs/hooks).
No source, candidate, stable, or live state was changed.

## Assignment bootstrap and continuation implementation contract

This is the implementation contract for the next architectural change.  It
is a development-only design record: it does not change the public contract,
the installed plugin, a candidate cache, or a live session.  The contract
preserves the complete fifteen-operation catalogue and all existing
clarification, plan approval, steering, assignment, report, documentation,
governance, locator, lease, projection, and closure behaviour.  It replaces
only the ambiguous host-to-worker handoff with a server-owned capability
protocol.

### 1. Authority and durable records

The Cortex ledger remains authoritative for task, contract revision,
assignment, decision, publication, and closure state.  Add a forward-only
schema migration for two server-private relations (public handles remain
opaque and are never reconstructed by a coordinator):

* `assignment_capabilities`: `capability_id` (opaque internal primary key),
  `task_id`, `delegation_id`, `task_ref_digest`, `delegation_ref_digest`,
  `dispatch_marker_digest`, `contract_revision`, `candidate_build_id`,
  `candidate_digest`, `kind` (`bootstrap` or `continuation`), `state`,
  `request_digest`, `issued_sequence`, `consumed_sequence`,
  `consumed_result_digest`, `created_at`, and `updated_at`.
* `assignment_capability_events`: append-only `event_id`, `capability_id`,
  `event_kind`, `request_digest`, `result_digest`, `sequence`, and timestamp.

Use foreign keys to the task/delegation rows, a unique logical-slot index on
`(delegation_id, kind, contract_revision, candidate_build_id,
candidate_digest)`, a lookup index on `(task_id, delegation_id, state)`, and a
unique event key on `(capability_id, request_digest, event_kind)`.  The
database must reject unknown states, duplicate active bootstrap capabilities,
or events whose sequence is not strictly increasing.  Existing rows are
forward-readable; no legacy public alias or inferred capability is added.
If an existing clarification-hold continuation column is retained during
migration, it becomes a projection of this relation, not a second authority;
new code must read the capability relation.

The persisted build identity is the already verified candidate receipt tuple:
`build_id`, `candidate_digest`, `source_digest`, and the exact registered
MCP-catalogue digest.  Assignment rows persist only these digests and opaque
references, never native agent names, raw prompts, process IDs, or raw
capability values.  A capability digest is the domain-separated hash of the
canonical tuple `(capability kind, task-ref digest, delegation-ref digest,
dispatch-marker digest, contract revision, candidate build/digest,
request digest)`.  The server mints it; callers may only return the exact
opaque value received in `structuredContent.handles`.

### 2. Minting, consume, replay, and failure state machine

`open_assignment` performs one transaction: validate the advertised schema;
verify task/revision/profile/routing relations; create the delegation and
dispatch marker; mint exactly one bootstrap capability for the logical slot;
persist its digest and candidate binding; construct the immutable
server-rendered worker message and its publication-next-action; append the
command receipt; then commit.  The response contains the exact `task_ref`,
`delegation_ref`, capability/assignment anchor, marker digest, candidate
identity, and rendered message.  A logical re-open returns the same pending
capability.  A materially changed task, subject, revision, or candidate is a
new logical slot; an accidental duplicate is a conflict, never a second
mutation.

`consume_assignment_evidence` is the worker's first semantic action.  In one
transaction it verifies the exact assignment anchor, task/delegation
relation, contract revision, marker digest, candidate identity, and request
digest, then records the consumption event and returns typed continuation
handles plus the bounded assignment evidence.  It must never accept a
reconstructed, shortened, UI-ellipsized, or assignment-ref-as-task-ref value.

The state transitions are deliberately finite:

```text
issued --exact first consume--> consumed
consumed --same request digest--> replay (no mutation)
issued --revision/build/identity mismatch--> stale (no mutation)
issued --different request or duplicate logical slot--> conflict (no mutation)
issued --host cannot provide supported child context--> unavailable
```

`replay`, `stale`, `conflict`, and `unavailable` are read-only outcomes.  An
ambiguous transport result is reconciled by reading the same capability and
event key; it never mints a replacement automatically.  A continuation
capability is minted only in the transaction that records the preceding
accepted decision/publication and is bound to the resulting contract or
publication revision.  No capability state implies native process resumption.

Clarification delivery follows the same rule: `record_clarification` records
the decision, updates the hold, and mints the exact continuation capability in
one commit.  `host_delivery` is a handoff receipt to the host, not a callback
or proof that a native worker resumed.  If delivery is unavailable, the hold
stays explicitly unavailable/pending and can be reconciled read-only.

### 3. Publication admission and closure

`publish_plan`, `publish_result`, and `publish_documentation` remain separate
semantic operations but share one closed evidence-fact envelope in their
advertised schemas.  Admission is a single transaction after schema
validation: verify the consumed predecessor capability, assignment role and
revision, exact required coverage, non-empty acceptance semantics, evidence
facts, documentation impact, and immutable content digest; insert the
publication and command receipt; advance the assignment projection; and
commit.  Incomplete evidence fails as `report_incomplete` before mutation;
malformed input is `validation_error`; changed replay is `conflict`; an exact
replay returns the original result.  The server, not a prompt, supplies the
next publication action.

There is one terminal publication per assignment and role.  Corrections use
an explicit new assignment/revision, preserving auditability.  Governance,
plan approval, documentation-impact assessment, initiative handling,
locator publication, projection, and closure consume these immutable
publications and retain their existing semantics.  Closure is admitted only
after required role publications, approvals/clarifications, governance, and
documentation assessment are present; it does not infer missing worker work.

All errors are bounded semantic codes with safe location/field/expected and
corrective-action metadata.  Event-journal entries contain only sanitized
one-way anchors, state transitions, and those semantic fields; never values,
raw arguments, prompts, native IDs, or exception text.

### 4. Host adapter boundary and supported worker context

The host adapter receives the exact server-rendered worker message and exact
opaque capability from the committed dispatch response.  It supplies child
role/context and the registered Cortex MCP server through the supported Agent
boundary, then reports only dispatch-attempt, first-semantic-action, and
stop/timeout observations.  It must not activate a second user route,
reconstruct tool arguments, fetch arbitrary skill files, or claim that an
MCP `server_ready` event proves model execution.  A first worker
`consume_assignment_evidence` success is the only durable bootstrap
acceptance proof.  `SubagentStart`, `SubagentStop`, text echoes, and MCP
startup are observational and may yield `delivery_unknown`/`unavailable`.
Same-user/host trust and non-atomic filesystem observation remain explicit
limitations; they are not upgraded into an authentication or process
identity guarantee.

### 5. Implementation ownership split

Implementer A owns `v12_maintenance.py`, domain/store transaction code,
capability relations and migrations, public schemas/structured-content
parity, publication admission, command receipts, and domain tests.  A owns
the fifteen-tool catalogue and all input/output semantic contracts; no
compatibility shim is permitted.

Implementer B owns the host adapter/launcher, worker-message provisioning,
Agent-boundary observations, lifecycle/event-journal projections, candidate
receipt export, and live transport tests.  B consumes A's typed dispatch
response byte-for-byte and does not alter ledger semantics.  The only shared
interface is the versioned dispatch-attempt/first-action observation type and
the exact server-rendered message; changes to it require an A-owned contract
test before either side proceeds.

### 6. Required qualification matrix

The source gate must include, with no unexplained failures, errors, or skips:

* fresh real-stdio first calls for all fifteen tools, closed-schema negative
  cases, structured-content/text parity, exact-handle reuse, and rejection of
  old shapes or MCP parameter recipes in skills/prompts;
* migration creation, foreign keys, unique logical slots, immutable event
  ordering, forward readability, candidate/build digest mismatch, and
  receipt provenance tests;
* bootstrap first-action success, wrong-first-action rejection, missing,
  shortened, replayed, tampered, cross-assignment, reordered, stale, conflict,
  unavailable, and ambiguous-transport reconciliation cases;
* clarification answer and plan-approval continuation, complete and
  incomplete plan/result/documentation admission, predecessor consumption,
  exact replay, changed replay, correction assignment, governance, locator,
  projection, and closure gates;
* host adapter tests proving exact message delivery, child-context
  provisioning, no fabricated native correlation/resume, sanitized lifecycle
  events, bounded timeout/stop handling, and no hidden worker tool error;
* candidate launcher/manifest/skill digest, package/catalog/lint, development
  artifact boundary, bytecode-injection, and real isolated tmux live-dev
  acceptance with LLM-owned clarification, approval, verification,
  documentation assessment, and closure decisions.

This sequence is the architecture: route/task activation -> coherent task
record -> server-owned assignment capability -> supported host child context
-> first evidence consumption -> role publication admission -> governance /
documentation -> closure.  Every transition is server-admitted and
idempotent; the host only transports and observes.  No existing orchestration
feature is removed or silently downgraded.

## References

- [MCP overview and security principles](https://modelcontextprotocol.io/specification/2025-06-18/index)
- [MCP lifecycle](https://modelcontextprotocol.io/specification/2025-03-26/basic/lifecycle)
- [MCP tools and output schemas](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)
- [MCP elicitation](https://modelcontextprotocol.io/specification/2025-06-18/client/elicitation)
- [MCP schema: roots, sampling, elicitation](https://modelcontextprotocol.io/specification/2025-06-18/schema)
- [MCP Tasks utility](https://modelcontextprotocol.io/specification/2025-11-25/basic/utilities/tasks)
- [OpenAI Codex plugin packaging](https://developers.openai.com/plugins/build/plugins)
- [OpenAI Codex MCP interface](https://github.com/openai/codex/blob/main/codex-rs/docs/codex_mcp_interface.md)
- [OpenAI Codex Hooks](https://learn.chatgpt.com/docs/hooks)
