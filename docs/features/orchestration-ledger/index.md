# Orchestration ledger, report bus, and lane lifecycle

<!-- GENERATED:START -->
## Purpose

The local MCP server implements the Cortex 2.0 task ledger, staged waves,
worker reports, and optional execution lanes through exactly one public tool:
`orchestrate`. The private `cortex/v7` primitives remain implementation
details; existing v7 tasks are inspectable and resumable.

## Key files and dependencies

- [cortex.py](../../../plugins/cortex/scripts/cortex.py) implements task, report, and lane tools.
- [profiles.json](../../../plugins/cortex/profiles.json) defines the 21 supported profiles and the `cortex/report/v1` field contract.
- [test_cortex_control.py](../../../tests/test_cortex_control.py) covers report-bus scoping/reconciliation and lane lifecycle behavior.

## Behavior and status

`orchestrate(start)` activates, classifies, initializes, persists the full plan,
and prepares the first wave. Each `orchestrate(advance)` accepts all completed
parallel work for the current wave, validates it before durable writes,
confirms actual host fields, stores strict eight-field `cortex/report/v1`
reports, evidence, and gate outcomes, then returns the next wave. It may
replace future waves; modifying completed work requires `allow_rework`. The
final advance reconciles reports and the project manifest, records the
documentation decision and server-observed close verification, creates the
handoff and audit record, and completes the task. Host `spawn_agent` and
user-authorized `create_thread` are still performed by Codex; they are never
public MCP lifecycle calls.

Reports are sanitized, task- and attempt-bound, and use one-use receipts.
Consuming a receipt writes an irreversible `reports/consumptions/` tombstone,
so reconciliation can repair derived receipts, indexes, and Markdown but
cannot replay consumed evidence. A report is capped at 64 KiB and 100 list
items per field; an attempt at 32 reports; a task at 256 reports and 1 MiB
total; and an attempt at 256 context grants. Every call includes an absolute
`project_root`; the same server process may serve multiple roots. Mutating
operations use `operations/<submission_id>.json` request-digest receipts, so
identical retries replay and changed payloads conflict. Expected façade
validation failures return `ok: false` and do not enter the exception log.
Profiles are preloaded and validated at MCP startup. Recovery and nested
operations are `inspect`, `resume`, `deactivate`, `lane`, `resource`, and
`question`.

Ledger, report-bus, and journal paths reject symlink ancestry and require regular-file targets, so journal or report-bus links cannot redirect state writes. Metrics reject negative token/elapsed values and non-finite or negative costs; telemetry retains a bounded tail of 1,000 events or 512 KiB and records evictions in `telemetry_dropped`. Multi-agent v2 is required for explicit per-worker model selection. Every delegation is evaluated independently from its declared work intent and risk: Luna handles explicit reading, discovery/data gathering, investigation, diagnosis, research, code review, CRUD-level edits, and small fixes at any risk; a read-only profile alone does not change the initial policy category, and non-analysis work such as architecture, migration, debugging, and implementation initially resolves to Terra before the exact model/effort remapping table is applied. Luna analysis/lightweight work defaults to and floors at medium effort for low/moderate risk, high for high risk, and xhigh for critical risk; explicit higher effort is preserved. Security task kind, the security gate, and the `security_auditor` profile initially resolve to Sol, then follow the same exact table; contradictory task kinds are normalized to security. Non-security Sol requires either a supported auditable extreme criterion and audit reference or a ledger-validated failed Terra attempt; free-form text is never authorization. Classification receipts are authoritative at initialization, so duplicate complexity and requirements inputs are ignored. Host completion and gate proof are separate: a passed attempt may be finalized before evidence linkage, while the gate remains blocked until required evidence is recorded. A unique context-grant id supplied where a report receipt is expected is corrected to that report's one-use receipt. Other `commit_gate` validation failures are recorded as bounded recovery events; after three failures for the same gate/mode the task becomes `blocked` with an explicit handoff/resume action instead of remaining active forever. Supported auditable-extreme criteria are `irreversible_multi_system_recovery`, `safety_critical_incident_response`, and `novel_cross_system_failure_without_bounded_rollback`. Reasoning effort is independently selected, with `none` normalized to `low`; only pairs outside the exact table retain the Sol high-effort floor. Lanes support creation, leases, task binding, resource claims, optional declared-worktree materialization, reconciliation, and clean retirement; managed dirty worktrees are refused during retirement.

## Verification

Run `python3 -m unittest discover -s tests -v`; the focused source-backed coverage is [test_cortex_control.py](../../../tests/test_cortex_control.py). Related project commands are in [verification.md](../../project/verification.md).
<!-- GENERATED:END -->
