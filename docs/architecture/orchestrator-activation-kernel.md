# Orchestrator activation kernel

Status: **architecture audit and design proposal**

This document isolates the first-call boundary from the full Cortex
orchestration engine. It is based on the packaged orchestrator/control skills,
the worker-message renderer, and the Phase D first-call root-cause record. It
does not change runtime code, public schemas, candidate state, or live-dev
transport.

## Finding

The current route has a semantic first-call rule, but no independent activation
kernel that can make the rule atomic. A model can receive the bundled skills,
write an activation acknowledgement, and remain in a conversational Working
state while taking no task-opening action. The full orchestration engine then
contains several concepts that are valid after anchoring but are not visibly
gated on the anchor:

- the orchestrator's bounded knowledge route requires reading project indexes;
- the root-resolution section permits a pre-ledger discovery worker when the
  root is not known;
- adaptive, governance, and output-validation overlays are described as
  loadable route behavior rather than post-anchor capabilities;
- the control skill's continuing-controller language emphasizes automatic
  reconciliation and dispatch but does not itself establish an activation
  state;
- the renderer proves worker-message/profile integrity, not route activation or
  task ownership.

The pre-ledger discovery-worker path is the clearest ordering contradiction:
worker dispatch is prohibited before task opening, yet the current root
resolution text allows a worker before the task when a root is needed. The
knowledge route is a second ambiguity: its reads are a deliberately bounded
coordinator exception, but the graph does not state that they are disabled
until the task anchor exists. These are not missing tool descriptions. They are
missing control-plane state.

## Architectural decision

Add a small host/coordinator activation kernel in front of the existing
orchestration engine. The kernel owns only route activation, one task-opening
boundary, and its receipt/reconciliation state. It does not own planning,
governance classification, knowledge routing, worker selection, worker
lifecycle, evidence acceptance, documentation impact, or closure.

After explicit route selection and packaged skill bootstrap, the kernel permits
exactly one task-opening operation using the live advertised contract. The
successful command receipt and its returned task anchor become the sole
authority for entering the full engine. No adaptation, governance assessment,
project knowledge read, assignment, native dispatch, or project-facing work is
available before that transition. A prose acknowledgement is neither a state
transition nor evidence of activation.

The kernel must be server/host-owned at the boundary where possible. The model
still chooses the route and composes the outcome contract, but it cannot create
a second logical opening after a successful or ambiguous opening. The kernel
does not reconstruct an anchor from visible text or durable evidence.

## State machine

```text
ORDINARY
   │ explicit cortex:orchestrator selection
   ▼
ROUTE_SELECTED
   │ packaged orchestrator/control context is present
   ▼
BOOTSTRAP_READY
   │ compose the complete outcome contract only
   │ (no project reads, overlays, governance, workers, or dispatch)
   ▼
OPENING_IN_FLIGHT ───────────────┐
   │ one semantic task opening    │ transport result is ambiguous
   │                              ▼
   │                        RECONCILING_READ_ONLY
   │                              │
   │                 ┌────────────┴────────────┐
   │                 │ committed               │ not proven committed
   ▼                 ▼                         ▼
ANCHORED       ANCHORED                    STOPPED
   │           (reuse the exact             (no new opening;
   │            returned anchor)             explain limitation)
   │ task-opening failure or missing anchor
   └────────────────────────────────────────► STOPPED

ANCHORED
   │ enable the existing orchestration engine
   ▼
ENGINE_ENABLED
   │ knowledge, governance, adaptation, DAG, assignments, workers,
   │ clarification, evidence, documentation, verification, closure
   ▼
CONTINUING
```

The `OPENING_IN_FLIGHT` state has one logical slot per selected route. A
successful receipt transitions that slot to `ANCHORED`; replay of the same
opening observes the existing receipt rather than opening another task. An
ambiguous transport result enters read-only reconciliation only. If
reconciliation cannot prove a committed opening, the kernel stops. It never
creates a new task as recovery.

## Required ordering contract

The instruction graph and host integration should make these orderings
structural and easy to observe:

| Before the anchor | After the anchor |
| --- | --- |
| Explicit route selection | Bounded knowledge routing |
| Packaged skill bootstrap | Advisory governance assessment |
| Outcome-contract composition | Adaptive pipeline decisions |
| One task-opening attempt | Planner and all other worker assignments |
| Read-only reconciliation after ambiguity | Native dispatch and worker lifecycle |
| Stop on unproven opening | Clarification, plan review, steering, evidence, documentation, verification, and closure |

The pre-anchor side must not contain a discovery-worker escape hatch. If the
canonical root is unavailable, the opening kernel stops or uses only the
already-authorized host context; root discovery cannot be delegated before a
task exists. The existing bounded knowledge exception moves to the anchored
side without changing its path allowlist or worker-only project boundary.

## Salience and renderer boundaries

The first semantic operation must be prominent in the route kernel, not merely
one row in the fifteen-operation catalogue. The full catalogue remains
available after anchoring and retains every existing capability. Supporting
skills should describe their behavior as post-anchor behavior and must not
present a preflight checklist that competes with task opening.

The worker renderer is downstream of activation. Its profile proof and trusted
or untrusted message boundary cannot serve as an activation receipt. A worker
brief is invalid as a bootstrap mechanism because dispatch itself is forbidden
until the task-opening receipt exists. Clarification continuation similarly
requires an already anchored task and existing assignment; it cannot bootstrap
the route.

## Host receipt boundary

The live host must expose a passive activation receipt before accepting a
workload. The receipt joins the exact isolated candidate identity, the ordinary
Codex registered Cortex server identity, and the advertised catalogue identity.
It contains no task prompt, call arguments, durable references, report data, or
private diagnostics. Transport exposes the receipt but does not parse it,
approve it, or decide acceptance; the coordinator/LLM verifies it.

This receipt answers a different question from the task-opening receipt:

| Receipt | Proves | Does not prove |
| --- | --- | --- |
| Passive host activation | The ordinary process registered the exact candidate and catalogue | That the model selected Cortex or opened a task |
| Task-opening command receipt | One durable task opening committed and returned the authoritative anchor | Worker dispatch, acceptance, or final outcome |
| Worker/profile renderer proof | The selected worker brief is trusted/profile-complete | Host lifecycle or route activation |

## Feature-preservation matrix

The kernel is a narrow gate, not a replacement orchestration engine. Every
existing capability remains enabled only after `ANCHORED`:

| Existing capability | Owner after activation | Preservation requirement |
| --- | --- | --- |
| Explicit route selection and English worker boundary | Coordinator/packaged skills | Route selection remains opt-in; workers remain English-only |
| Bounded project knowledge routing | Coordinator exception | Same closed indexes/linked-page rule, moved after the task anchor |
| C1/C2/C3 and advisory depth | Model + governance operation | Kernel does not classify, promote, or veto |
| Dynamic DAG and parallel non-overlapping work | Coordinator | No universal waves or backend scheduler introduced |
| Planner, plan review, approval, clarification, steering | Coordinator + semantic operations | Existing holds, exact answers, revisions, and same-worker continuation remain |
| Assignment ownership and native worker lifecycle | Coordinator/host | Assignment creation and spawn remain distinct and reconciled |
| Worker publication and evidence consumption | Owning workers | Typed evidence, predecessor barriers, immutable publication, and rework remain |
| Documentation impact and documentation workers | Workers/coordinator routing | Impact assessment and no-impact evidence remain required |
| Verification, hidden worker events, and live acceptance | LLM verifier/host observation | Transport remains observation-only; verifier owns acceptance |
| Initiatives, governance, closure, final synthesis | Coordinator + advisory ledger | No capability becomes a backend admission gate |
| Isolated candidate delivery and live-dev | Host/operator | Passive receipt is added; candidate refresh and stable environment remain unchanged |

## Acceptance criteria for implementation

1. A route-selected session cannot dispatch a worker, perform a bounded project
   read, assess governance, apply an adaptation overlay, or perform project
   work before one successful task-opening receipt exists.
2. A prose activation acknowledgement never advances kernel state.
3. A task-opening failure stops the route without degraded project work.
4. An ambiguous opening result performs only read-only reconciliation and never
   creates a second task.
5. A successful opening is idempotent by its command receipt and exposes one
   authoritative anchor for every later task-scoped action.
6. The passive host receipt is required before live workload submission and is
   exposed without transport-side interpretation.
7. After anchoring, the feature-preservation matrix remains fully covered by
   source, candidate, and LLM-driven live tests.
8. Skills, profiles, renderers, and workload prompts continue to omit MCP
   argument names, request shapes, field inventories, limits, and sample
   payloads; the active catalogue remains their sole call-contract authority.

