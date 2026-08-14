# Orchestration ledger, report bus, and lane lifecycle

<!-- GENERATED:START -->
## Purpose

The local MCP server implements the `cortex/v7` task ledger, staged gates, delegation receipts, worker reports, and optional execution lanes.

## Key files and dependencies

- [cortex.py](../../../plugins/cortex/scripts/cortex.py) implements task, report, and lane tools.
- [profiles.json](../../../plugins/cortex/profiles.json) defines the 21 supported profiles and the `cortex/report/v1` field contract.
- [test_cortex_control.py](../../../tests/test_cortex_control.py) covers report-bus scoping/reconciliation and lane lifecycle behavior.

## Behavior and status

`record_report` stores sanitized task- and attempt-bound reports and creates one-use receipts. Consuming a receipt writes an irreversible `reports/consumptions/` tombstone, so reconciliation can repair derived receipts, indexes, and Markdown but cannot replay consumed evidence. A report is capped at 64 KiB and 100 list items per field; an attempt at 32 reports; a task at 256 reports and 1 MiB total; and an attempt at 256 context grants. Recovery from a stranded record allocates a fresh record number without overwriting the existing authoritative JSON. An activation becomes bound to the initialized task and active-thread mapping; task operations require that same binding. `record_delegation` creates an `awaiting_host_spawn` attempt and a complete native `spawn_agent` request, including the selected profile instructions. The main Codex agent must invoke that host tool and then call `confirm_host_spawn` with the returned child id before the attempt becomes `running`; a failed native spawn is finalized as non-success rather than represented as a worker. The recorded id is coordinator-supplied correlation, not an independent host attestation. Real orchestration binds the absolute `project_root` on activation, restores it for later calls, and confirms `${project_root}/.codex/cortex` through activation, classification, initialization, and status before any project operation or dispatch; it fails closed on an unavailable server, failed/mismatched/unwritable root, a set `CORTEX_ROOT`, or `/tmp` fallback. Recoverable stale revision, receipt, and gate hints produce correction metadata rather than MCP errors.

Ledger, report-bus, and journal paths reject symlink ancestry and require regular-file targets, so journal or report-bus links cannot redirect state writes. Metrics reject negative token/elapsed values and non-finite or negative costs; telemetry retains a bounded tail of 1,000 events or 512 KiB and records evictions in `telemetry_dropped`. Multi-agent v2 is required for explicit per-worker model selection. Every delegation is evaluated independently from its profile, task kind, and risk: Luna handles reading, discovery/data gathering, CRUD-level edits, and small fixes at low or moderate risk regardless of parent task complexity; every other non-security dispatch uses Terra. Security task kind, the security gate, and the `security_auditor` profile always dispatch to Sol, normalizing contradictory task kinds to security. Non-security Sol requires either a supported auditable extreme criterion and audit reference or a ledger-validated failed Terra attempt; free-form text is never authorization. Classification receipts are authoritative at initialization, so duplicate complexity and requirements inputs are ignored. Host completion and gate proof are separate: a passed attempt may be finalized before evidence linkage, while the gate remains blocked until required evidence is recorded. Supported auditable-extreme criteria are `irreversible_multi_system_recovery`, `safety_critical_incident_response`, and `novel_cross_system_failure_without_bounded_rollback`. Reasoning effort is independently selected, with `none` normalized to `low` and Sol using at least high effort. Lanes support creation, leases, task binding, resource claims, optional declared-worktree materialization, reconciliation, and clean retirement; managed dirty worktrees are refused during retirement.

## Verification

Run `python3 -m unittest discover -s tests -v`; the focused source-backed coverage is [test_cortex_control.py](../../../tests/test_cortex_control.py). Related project commands are in [verification.md](../../project/verification.md).
<!-- GENERATED:END -->
