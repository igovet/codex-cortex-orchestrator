---
name: cortex-control
description: Use this skill when coordinating a non-trivial task across Codex agents and durable gate, delegation, lock, or handoff state is useful. It uses the local cortex MCP server; apply Cortex model policy and bounded coordinator overrides at dispatch time.
---

# Cortex Control

Cortex v3 exposes three coordinator lifecycle operations plus scoped report
transport. Coordinators use `start_orchestration` and
`continue_orchestration` for normal work, `read_worker_report` to evaluate a
persisted report, and `manage_orchestration` only for recovery or rare
subsystems. Workers use only `record_report`; they must not call lifecycle
operations. The private v7 ledger API and retired public `orchestrate` facade
must never be called by a coordinator or worker. Cortex remains explicitly
opt-in through a non-help, non-`normal` `cortex:orchestrator` route.

## Root coordinator lock

An active Cortex root is coordination-only. It must not use project-reading,
search, shell, filesystem, patch, build, test, or execution tools on the target
project. It may call Cortex, invoke the exact returned worker dispatches, wait,
route questions, assess reports, and communicate with the user. Every project
operation must be delegated, including investigation after a plan and small or
obvious edits. The root must remain idle while a worker runs. Worker failure,
delay, unavailable dispatch, or incomplete evidence is a blocker or rework
signal, never permission for the root to perform the work directly.

## Normal flow

1. Call `start_orchestration` once with the exact absolute `project_root` and
   `task.objective`. Add requirements, acceptance criteria, scope, allowed
   paths, verification, budget, pause conditions, language, or complexity only
   when useful. Complexity defaults to C2 and accepts aliases.
2. The coordinator owns the pipeline decision. It may consciously accept the
   standard quality-preserving pipeline or supply `waves`; Cortex stores,
   returns, and validates that plan and enforces documentation and close. An override uses only
   `waves: [{workers: [{phase, ...}]}]`. `phase` is required; optional fields
   are `profile`, `objective`, `paths`, `acceptance`, `verification`, `model`,
   `user_requested_model`, `effort`, `depends_on`, `context_files`, `visible`,
   and `isolated_checkout`. `depends_on`
   names exact completed or earlier prerequisite phases; omit it to receive all
   verified predecessor reports. `context_files` carries exact project/feature
   knowledge pages selected by the planner for that worker.
3. Invoke each returned dispatch with its exact `call` and `arguments`.
   Before invoking it, check the sibling `phase`, `profile`, `capability`,
   `sandbox`, and `selection_reason` against the latest worker evidence and
   the canonical roster in `cortex:orchestrator`. Arguments contain only
   native host parameters. Never add ledger IDs or copy an expected model into
   a missing native `model` field.
4. Workers do not call lifecycle operations. They use the native parent channel
   for questions, publish one strict `cortex/report/v1` through `record_report`,
   and return only `REPORT_RECORDED report_ref=<value>` plus at most a
   two-sentence summary. They must never paste the report JSON into the parent
   channel. When predecessor handoffs are supplied, they review all of them and
   include the generated `Predecessor review:` acknowledgement in report
   evidence; the report tool enforces complete acknowledgement.
5. After all workers finish, read every ref with `read_worker_report`, evaluate
   the reports against the pipeline, then call `continue_orchestration` exactly
   once with `project_root`, the opaque `task_ref` and relative `step` from the
   prior response, and all `report_ref` results. A single result needs no worker reference.
   Parallel results repeat only the returned integer `worker` slot. Omit
   status for success; non-success requires normalized `status` and `reason`
   and omits report fields. Until all workers finish, remain idle and perform
   no project operation.
6. Repeat one continue per completed wave. Finish only when `outcome` is
   `completed`; Cortex has then reconciled reports, evidence, documentation,
   close verification, the manifest, and handoff.

Normal requests never carry caller-generated submission, task, wave, attempt,
principal, thread, host-tool, host-model, or host-effort fields. Internal IDs
and receipts remain durable below `.codex/cortex`.

## Idempotency and relative references

Start resumes an identical unfinished request automatically. Continue replays
a byte-identical retry for its internal active wave. The relative `step`
distinguishes identical report content used on successive waves without
exposing durable identity. Parallel worker slots are complete, unique, and
validated atomically before task state changes.

Preserve the `task_ref` returned by start and pass it on later lifecycle and
report-read calls. Different task contracts can run concurrently below one
project root; the project registry is lock-serialized and task records remain
isolated. An exact duplicate active start is an idempotent replay. If a caller
omits the ref while several tasks are active, Cortex returns `needs_selection`
with objectives and opaque refs instead of guessing.

## Adaptation and recovery

The returned `pipeline.waves` snapshot is the current coordinator-owned plan.
Follow it by default. Pass compact `future_waves` only when the coordinator
decides that verified evidence materially changes work that has not started;
include a concise `reason`. Planner and explorer ownership recommendations are
advisory routing evidence, not an automatic rewrite command. Prefer the
narrowest supported profile and replace a stale route only after that explicit
decision. `general` is a conservative fallback, not the preferred universal
writer. Repeating a completed phase requires `rework: true`. Use
`manage_orchestration` for `inspect`, `resume`, `deactivate`, `lane`,
`resource`, or `question`; these intents do not belong in normal wave calls.
Follow recoverable diagnostics and never fall back to private tools.

The question intent can request MCP UI elicitation through
`elicitation/create`. Worker questions normally use the native parent channel;
use management when a durable main-UI question is required. Lack of advertised
host elicitation support is a host limitation, not permission to invent an
answer.

## Dispatch and evidence policy

Use the simplified model policy. `explorer` always selects Luna; the
coordinator chooses its effort, with the risk-based default used when omitted,
and Terra is permitted only as the hidden host-unavailable fallback.
The complete effort vocabulary is `low`, `medium`, `high`, `xhigh`, and `max`;
never request another value. `planner` defaults to Luna at exactly `max`, and
the coordinator may normally select Terra from `medium` through `max`. Every
remaining non-security profile has the same exact Luna `max` default and normal
Terra `medium`-through-`max` override. Luna `max` is already a powerful default;
do not escalate it reflexively. Security context, the security gate, and
`security_auditor` always select Sol, with minimum effort `medium` for C1,
`high` for C2, and `xhigh` for C3, capped at `max`. Non-security Sol is accepted
only when the user explicitly selected it.
Set compact `user_requested_model: sol`; omit `model` or also set it to `sol`.
Cortex records matching `user_requested_model` and `requested_model`.
Coordinator preference, an earlier Terra failure, and auditable-extreme labels
do not authorize Sol;
the retired `sol_escalation` and model/effort remapping contracts must not be
used.

Configured-default Luna routes carry explicit effort but omit native `model`;
explicit Luna/Terra/Sol selections retain it. If Luna is unavailable to the
host, Cortex may use a hidden Terra fallback while preserving the selected
effort. Expected routes are metadata, not proof of the effective host model.
Only host-observed runtime metadata may attest actual models. Workers emit
English only; the main coordinator localizes user-facing content.

Generated worker briefings carry a bounded Codebase Memory contract. When the
`mcp__codebase_memory__*` tools are actually available, workers resolve the
project through `list_projects` by exact `project_root`, prefer graph,
architecture, trace, and impact tools for non-trivial discovery, and confirm
consequential facts in current source or tests. Designated read-only discovery
profiles may refresh one missing or stale index; other profiles fall back to
repository-native tools. One failed MCP attempt is enough: record the
limitation and do not loop. The coordinator never calls repository-intelligence
tools itself because the root lock still applies.

When `docs/project/index.md` or `docs/features/index.md` exists, Cortex adds it
to every worker briefing without asking the coordination-only root to inspect
the project. The planning worker reads both indexes first, selects all linked
pages relevant to the task boundary, and records the recommended paths in its
report. The coordinator attaches those paths through later-wave
`context_files`; downstream workers also re-check the indexes for missed
cross-feature dependencies. Documentation is a navigation layer and prior,
not authority: workers confirm consequential claims in current source, tests,
schemas, or executable configuration. Every worker report must include one
`Knowledge reviewed:` evidence entry naming both available indexes and every
additional knowledge page actually used. The report tool rejects an omitted
index acknowledgement.

Canonical phases are `plan`, `discover`, `architecture`,
`database_architecture`, `implementation`, `qa`, `security`, `performance`,
`accessibility`, `ux`, `review`, `documentation`, and `close`. One phase may
appear in only one wave; multiple owners for a phase share that wave. Generic
`verification` maps to `qa`, while `build_verification` and
`final_verification` map to `close`.

Every report remains quota-, redaction-, path-, and receipt-checked. Cortex
fails closed on root or symlink violations, stale steps, invalid slots,
changed retries, missing sections, invalid rework, failed close verification,
manifest mismatch, incomplete predecessor or knowledge-index acknowledgement,
or handoff context that exceeds its safe count/size budget. It never silently drops an older
report; narrow the dependency set with `depends_on`.

## Durable artifacts

Every call supplies its exact absolute `project_root`. Runtime state stays in
`${project_root}/.codex/cortex` using compatible `cortex/v7` ledgers.
`CORTEX_ROOT`, `/tmp` fallback, and symlink traversal remain forbidden.
