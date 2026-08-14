# Orchestration ledger, report bus, and lane lifecycle

<!-- GENERATED:START -->
## Purpose

The local MCP server implements the Cortex 3.1 task ledger, staged waves,
worker reports, and optional execution lanes through three public tools:
`start_orchestration`, `continue_orchestration`, and `manage_orchestration`.
The private `cortex/v7` primitives and legacy v2 facade remain compatibility
details; existing v7 tasks are inspectable and resumable through the v3 adapter.

## Key files and dependencies

- [cortex.py](../../../plugins/cortex/scripts/cortex.py) implements task, report, and lane tools.
- [profiles.json](../../../plugins/cortex/profiles.json) defines the 21 supported profiles, their gate assignments, the 13 gate briefings, and the `cortex/report/v1` field contract.
- [test_cortex_control.py](../../../tests/test_cortex_control.py) covers report-bus scoping/reconciliation and lane lifecycle behavior.

## Behavior and status

`start_orchestration` accepts an absolute `project_root` and compact task
contract, defaults complexity to safe C2, builds the standard pipeline when
waves are omitted, and prepares the first wave. Each
`continue_orchestration` call supplies the relative active-wave `step` and
strict worker reports. A single-worker wave needs no slot; a parallel wave
uses short relative `worker: 1..N` slots. The server validates completeness,
uniqueness, and ownership atomically before state writes, then returns the
next step and native dispatch arguments. Future-wave replacement and explicit
rework retain invalidation semantics; a semantically unchanged replacement is
recorded as `unchanged` instead of failing after gate writes, and relative
future steps remain monotonic. Human-readable language aliases such as
`English` normalize before ledger creation. `manage_orchestration` is reserved for
inspect/resume/deactivate, lanes, resources, and durable questions; it is not
part of normal wave progression. Host `spawn_agent` and user-authorized
`create_thread` are still performed by Codex, never by public MCP lifecycle
calls.

Worker prompts have three deliberate layers: the role-specific professional
playbook from the selected profile, the overall task assignment and context,
and the current gate mission with its ownership, acceptance, and verification
defaults. Task-level requirements and validation stay distinct from gate-level
criteria. Explicit coordinator-supplied objective, ownership, acceptance, or
verification values override the corresponding gate defaults; omitted values
are filled from the validated briefing registry. Context files and explicitly
granted predecessor reports are included in the assignment so workers can
ground their work without inventing missing context.

The `planner` profile is read-only and follows a repository-grounded,
decision-complete workflow: it resolves discoverable facts, separates product
decisions from repository evidence, closes interfaces/data flow/failure,
compatibility, validation, rollout, and ownership concerns, and asks only
questions that materially change scope or behavior. Its plan must leave the
implementer no unmade design decisions and must cite evidence for consequential
choices.

Reports are sanitized, task- and attempt-bound, and use one-use receipts.
Consuming a receipt writes an irreversible `reports/consumptions/` tombstone,
so reconciliation can repair derived receipts, indexes, and Markdown but
cannot replay consumed evidence. A report is capped at 64 KiB and 100 list
items per field; an attempt at 32 reports; a task at 256 reports and 1 MiB
total; and an attempt at 256 context grants. Every call includes an absolute
`project_root`; the same server process may serve multiple roots. Mutating v3
operations use server-owned request-digest receipts tied to the internal
active wave, so identical retries replay and changed or stale payloads
conflict before partial writes. Expected facade validation failures return
`ok: false` and do not enter the exception log. Host model/tool/effort values
are selected routing metadata; v3 does not claim actual host attestation
unless the host supplies observable evidence.
Profiles and all 13 gate briefings are preloaded and validated at MCP startup;
invariant coverage checks that all 21 playbooks contain the required
professional sections and that every gate briefing has non-generic acceptance
and verification lists. Recovery and nested
operations are `inspect`, `resume`, `deactivate`, `lane`, `resource`, and
`question`.

Ledger, report-bus, and journal paths reject symlink ancestry and require regular-file targets, so journal or report-bus links cannot redirect state writes. Metrics reject negative token/elapsed values and non-finite or negative costs; telemetry retains a bounded tail of 1,000 events or 512 KiB and records evictions in `telemetry_dropped`. Multi-agent v2 is required for explicit per-worker model selection. Every delegation is evaluated independently from its declared work intent and risk: Luna handles explicit reading, discovery/data gathering, investigation, diagnosis, research, code review, CRUD-level edits, and small fixes at any risk; a read-only profile alone does not change the initial policy category, and non-analysis work such as architecture, migration, debugging, and implementation initially resolves to Terra before the exact model/effort remapping table is applied. Luna analysis/lightweight work defaults to and floors at medium effort for low/moderate risk, high for high risk, and xhigh for critical risk; explicit higher effort is preserved. Security task kind, the security gate, and the `security_auditor` profile initially resolve to Sol, then follow the same exact table; contradictory task kinds are normalized to security. Non-security Sol requires either a supported auditable extreme criterion and audit reference or a ledger-validated failed Terra attempt; free-form text is never authorization. Classification receipts are authoritative at initialization, so duplicate complexity and requirements inputs are ignored. Host completion and gate proof are separate: a passed attempt may be finalized before evidence linkage, while the gate remains blocked until required evidence is recorded. A unique context-grant id supplied where a report receipt is expected is corrected to that report's one-use receipt. Other `commit_gate` validation failures are recorded as bounded recovery events; after three failures for the same gate/mode the task becomes `blocked` with an explicit handoff/resume action instead of remaining active forever. Supported auditable-extreme criteria are `irreversible_multi_system_recovery`, `safety_critical_incident_response`, and `novel_cross_system_failure_without_bounded_rollback`. Reasoning effort is independently selected, with `none` normalized to `low`; only pairs outside the exact table retain the Sol high-effort floor. Lanes support creation, leases, task binding, resource claims, optional declared-worktree materialization, reconciliation, and clean retirement; managed dirty worktrees are refused during retirement.

## Verification

Run `python3 -m unittest discover -s tests -v`; the focused source-backed coverage is [test_cortex_control.py](../../../tests/test_cortex_control.py). Related project commands are in [verification.md](../../project/verification.md).
<!-- GENERATED:END -->
