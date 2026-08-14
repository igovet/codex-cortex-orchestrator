---
name: cortex-control
description: Use this skill when coordinating a non-trivial task across Codex agents and durable gate, delegation, lock, or handoff state is useful. It uses the local cortex MCP server; choose each subagent's model and reasoning effort dynamically at dispatch time.
---

# Cortex Control

Cortex v3 exposes three public MCP tools. Coordinators use
`start_orchestration` and `continue_orchestration` for normal work and
`manage_orchestration` only for recovery or rare subsystems. The private v7
ledger API and retired public `orchestrate` facade must never be called by a
coordinator or worker. Cortex remains explicitly opt-in through a non-help,
non-`normal` `cortex:orchestrator` route.

## Normal flow

1. Call `start_orchestration` once with the exact absolute `project_root` and
   `task.objective`. Add requirements, acceptance criteria, scope, allowed
   paths, verification, budget, pause conditions, language, or complexity only
   when useful. Complexity defaults to C2 and accepts aliases.
2. Usually omit `waves`; Cortex builds a quality-preserving pipeline and
   enforces documentation and close. An override uses only
   `waves: [{workers: [{phase, ...}]}]`. `phase` is required; optional fields
   are `profile`, `objective`, `paths`, `acceptance`, `verification`, `model`,
   `effort`, `visible`, and `isolated_checkout`.
3. Invoke each returned dispatch with its exact `call` and `arguments`.
   Arguments contain only native host parameters. Never add ledger IDs or copy
   an expected model into a missing native `model` field.
4. Workers do not call Cortex. They use the native parent channel for
   questions and return one strict `cortex/report/v1` object with exactly
   `summary`, `findings`, `questions`, `changed_files`, `tests`, `evidence`,
   `uncertainty`, and `next_action`.
5. After all workers finish, call `continue_orchestration` exactly once with
   `project_root`, the relative `step` from the prior response, and all
   results. A single result needs no worker reference. Parallel results repeat
   only the returned integer `worker` slot. Omit status for success;
   non-success requires normalized `status` and `reason` and omits the report.
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

When one task is active Cortex selects it automatically. If several tasks are
active it returns `needs_selection` with objectives and opaque `task_ref`
values. Repeat only the next ambiguous or recovery call with the chosen ref.

## Adaptation and recovery

Pass compact `future_waves` to continue to replace work that has not started.
Repeating a completed phase requires `rework: true`. Use
`manage_orchestration` for `inspect`, `resume`, `deactivate`, `lane`,
`resource`, or `question`; these intents do not belong in normal wave calls.
Follow recoverable diagnostics and never fall back to private tools.

The question intent can request MCP UI elicitation through
`elicitation/create`. Worker questions normally use the native parent channel;
use management when a durable main-UI question is required. Lack of advertised
host elicitation support is a host limitation, not permission to invent an
answer.

## Dispatch and evidence policy

Profiles do not pin model or effort. Configured-default Luna routes carry
explicit effort but omit native `model`; explicit Luna/Terra/Sol overrides
retain it. Expected routes are metadata, not proof of the effective host
model. Only host-observed runtime metadata may attest actual models. Workers
emit English only; the main coordinator localizes user-facing content.

Every report remains quota-, redaction-, path-, and receipt-checked. Cortex
fails closed on root or symlink violations, stale steps, invalid slots,
changed retries, missing sections, invalid rework, failed close verification,
or manifest mismatch.

## Durable artifacts

Every call supplies its exact absolute `project_root`. Runtime state stays in
`${project_root}/.codex/cortex` using compatible `cortex/v7` ledgers.
`CORTEX_ROOT`, `/tmp` fallback, and symlink traversal remain forbidden.
