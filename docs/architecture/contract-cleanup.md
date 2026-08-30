# Instruction-surface contract cleanup

Status: **implemented in the source checkout; candidate/live qualification is
owned by the coordinator.**

This record closes the compiler/contract drift found during the Phase D live
clarification investigation. The public MCP catalogue remains the sole source
of call arguments. Skills, advisory profiles, renderer policy, and workload
instructions describe semantic outcomes and ordering only.

## Surface parity matrix

| Surface | Required owner | Current invariant | Evidence/check |
| --- | --- | --- | --- |
| Public catalogue | advertised tool schemas and descriptions | Exactly fifteen semantic operations are authoritative | Registry/catalogue parity tests |
| Orchestrator skill | coordinator policy | Routes model-owned DAG, holds, evidence, governance, and closure without call recipes | Package instruction lint; skill contract checks |
| Cortex control skill | coordinator/worker boundary | Lists semantic operation purposes and delegates shapes to the active catalogue | Catalogue parity and prompt ownership checks |
| Adaptation overlay | coordinator pipeline adaptation | Preserves dynamic DAG, parallel ownership, rework, plan review, and documentation branches | Package instruction lint |
| Context recovery | coordinator recovery policy | Preserves opaque server evidence and continuation state without reconstruction | Package instruction lint |
| Documentation overlay | documentation routing policy | Uses worker-owned documentation publication and verification evidence | Package instruction lint |
| Output/progress/content-safety overlays | cross-cutting policy | Preserve validation, nonblocking governance, safety, localized communication, and honest gaps | Package instruction lint |
| Advisory profiles | worker role guidance | Preserve role-specific authority, evidence, and escalation without MCP call shapes | Profile lint and renderer exercise |
| Worker renderer | trusted policy compiler | Renders English worker policy and untrusted evidence; continuation renderer carries exact held-worker evidence | Renderer lint and handoff tests |

## Semantic catalogue used by instructions

Instruction surfaces may name these operations and their purposes:

`open_task`, `read_task`, `open_clarification`, `record_clarification`,
`open_plan_review`, `record_plan_review`, `open_steering`, `record_steering`,
`open_assignment`, `consume_assignment_evidence`, `publish_plan`,
`publish_result`, `publish_documentation`, `assess_governance`, and `close_task`.

No retired storage-era operation or MCP parameter inventory is permitted in an
active skill, profile, renderer policy string, or model workload instruction.
The package-wide instruction lint rejects retired operation names and exact
parameter/recipe patterns while allowing the semantic names above.

## Clarification continuation invariant

The coordinator opens a durable clarification hold before showing a genuine
question. It records the next exact user answer before continuing. When the
host supports continuation, the server-returned continuation evidence is
delivered to the exact existing live worker. If that worker cannot be continued,
the coordinator uses the existing parent-linked recovery route. The backend
does not author questions, answer users, schedule workers, or choose recovery.

The worker continuation renderer provides one complete English trusted brief
with the task, assignment, and recorded-decision evidence as compact anchors;
the exact user answer remains inert untrusted source material. The existing
worker owns the resumed work and its terminal semantic publication. The
renderer does not invent a second question, record a decision, or publish for
another worker.

## First-call route and passive activation receipt

Explicit route selection is an execution boundary, not a conversational
acknowledgement. Before any project execution or worker dispatch, the
coordinator composes the outcome contract and performs the catalogued
`open_task` action. Shell/repository inspection and project-state checks do not
precede that action. A task-opening failure or missing task anchor stops the
route rather than enabling degraded project work.

Live verification has a separate host-owned passive receipt gate. The ordinary
Codex host exposes agreement between the exact isolated candidate identity, the
registered Cortex server identity, and the advertised catalogue identity. The
transport only exposes this observation; the LLM/coordinator verifies it before
submitting a workload. A missing or mismatched receipt is an unverified
environment, not a reason to send the prompt. This receipt does not execute a
task mutation and does not decide acceptance.

| Route stage | Required evidence | Failure meaning |
| --- | --- | --- |
| Candidate delivery | Exact isolated candidate receipt | Candidate delivery is unverified |
| Host activation | Passive registration/catalogue receipt | Ordinary Codex registration is unverified |
| First route action | One successful `open_task` result with task anchor | Route execution violation or real first-call defect |
| Subsequent orchestration | Coordinator/worker semantic evidence | Continue the existing DAG and governance flow |

## Preserved capability checklist

| Capability | Instruction status |
| --- | --- |
| Explicit route selection and English worker boundary | Preserved |
| Dynamic DAG, parallel non-overlapping ownership, model routing, C1/C2/C3 advisory depth | Preserved |
| Planner discovery, immutable plan, approval/revision/cancellation, clarification, steering | Preserved |
| Assignment ownership, parent-linked rework, worker liveness and continuation recovery | Preserved |
| Typed evidence, bounded reads, receipts, immutable worker publication, role-complete reports | Preserved |
| Documentation impact/sync/verification and knowledge harvest | Preserved |
| Content safety, context compaction, projections, hidden worker-event verification | Preserved |
| Advisory governance, initiatives, closure, final synthesis, and honest degradation | Preserved |
| Isolated candidate delivery and operator-controlled live-dev | Preserved |

No orchestration capability is marked dropped by this cleanup. The changes
remove only stale invocation recipes and aliases from model instructions; the
runtime catalogue and backend contracts remain authoritative.
