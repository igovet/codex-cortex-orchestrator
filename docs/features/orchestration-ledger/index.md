# Orchestration ledger, report bus, and lane lifecycle

<!-- GENERATED:START -->
## Purpose

The local MCP server implements the Cortex 3.2.2 task ledger, staged waves,
worker reports, and optional execution lanes through exactly five public
tools: coordinator lifecycle operations `start_orchestration`,
`continue_orchestration`, and `manage_orchestration`, worker-only
`record_report`, and coordinator-only `read_worker_report`.
The private `cortex/v7` primitives and legacy v2 facade remain compatibility
details; existing v7 tasks are inspectable and resumable through the v3 adapter.

## Key files and dependencies

- [cortex.py](../../../plugins/cortex/scripts/cortex.py) implements task, report, and lane tools.
- [profiles.json](../../../plugins/cortex/profiles.json) is the canonical machine-validated source for all 21 profiles, their descriptions, sandboxes, route categories, gates, selection/avoidance guidance, ordered implementation routing, 13 gate briefings, and the `cortex/report/v1` field contract.
- [test_cortex_control.py](../../../tests/test_cortex_control.py) covers report-bus scoping/reconciliation and lane lifecycle behavior.

## Behavior and status

`start_orchestration` accepts an absolute `project_root` and compact task
contract, defaults complexity to safe C2, builds the standard pipeline when
waves are omitted, and prepares the first wave. Each
`continue_orchestration` call supplies the relative active-wave `step`, the
opaque `task_ref`, and persisted worker `report_ref` values. A single-worker
wave needs no slot; a parallel wave
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

Cortex returns `task_ref` on every task-bound lifecycle response. The
coordinator preserves it on every later lifecycle and report-read call. Different task and
wave contracts can run concurrently below one project root; an exact duplicate
active start replays idempotently, while changed content creates a distinct
task. Omitting the ref when several tasks are selectable returns
`needs_selection` with bounded objective/ref candidates. The project registry
is lock-serialized so concurrent process starts do not overwrite one another.

The coordinator builds or consciously accepts the initial pipeline and follows
the returned snapshot by default. Planner and explorer findings are advisory;
only the coordinator may replace not-yet-started `future_waves`, and only when
verified evidence materially changes ownership, dependencies, risk,
sequencing, or validation. Every replacement includes a concise reason.
Bounded phase aliases normalize `implement` to `implementation` and
`build_verification` to final `close`; the server also rejects a canonical
phase repeated across later waves, preventing correction/retry loops caused by
relabeling the same work.

While a Cortex task is active, the main/root agent is coordination-only. It
may use Cortex lifecycle calls, launch only the exact returned worker
dispatches, wait, evaluate reports, route questions, and communicate with the
user. It must never inspect, search, read, edit, patch, build, test, or run the
target project and must remain idle while workers run. Worker delay, failure,
or unavailability is handled through recovery, rework, or a blocker; it never
authorizes direct root project work. `SessionStart` and every public v3
`next_action`, including caller-correctable failures, reassert this lock.

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

Automatic implementation routing examines only bounded explicit signals in
the task objective, requirements, acceptance criteria, scope, allowed paths,
and verification. It recognizes relevant English and Russian signals and
selects conservatively in this order: `fullstack_dev`, `mobile_dev`,
`devops_engineer`, `data_engineer`, `debugger`, `refactorer`, `frontend_dev`,
then `backend_dev`, with `general` only when no specialist signal is strong
enough. The initial selection is provisional rather than a substitute for
repository evidence: `planner` or `explorer` may recommend a narrower owner and
the coordinator alone decides whether to replace not-yet-started
`future_waves`, with a concise evidence-based reason. Both
profiles receive the complete generated team catalog; the root orchestrator
skill carries the same generated roster and routing rules while the root
remains coordination-only.

The compact public worker schema exposes the exact enum of all 21 canonical
profile names. Legacy aliases remain compatibility input only. Cortex rejects
a profile that cannot own the requested phase before creating ledger state.
Every dispatch reports `phase`, `profile`, `capability`, `sandbox`, and
`selection_reason` separately from the unchanged native `call` arguments, so
the coordinator can audit routing without rewriting the host request.

Every worker calls only `record_report` to persist exactly `summary`,
`findings`, `questions`, `changed_files`, `tests`, `evidence`, `uncertainty`,
and `next_action`. Its successful native final is only
`REPORT_RECORDED report_ref=<value>` plus at most a two-sentence summary; a
tool failure returns only the exact error. The coordinator
reads the full record through `read_worker_report` and advances with the ref,
never an inline report body. If the worker is interrupted after persistence but
before its acknowledgement, `manage_orchestration` inspect returns the compact
entry in `available_reports` for recovery.

Predecessor handoffs are an enforced worker contract. Omitted `depends_on`
supplies every verified predecessor report, an explicit phase list selects only
those completed or earlier-wave dependencies, and `[]` declares intentional
independence. The generated prompt requires reconciliation of every supplied
handoff and an exact generated `Predecessor review:` evidence marker containing
all report refs; public `record_report` rejects incomplete acknowledgement.
More than eight reports or a predecessor payload beyond the safe context budget
fails closed with guidance to narrow `depends_on`, never by silently dropping
older reports.

Codebase Memory is conditional worker tooling rather than a ledger dependency.
When the tools are available, the worker resolves the task project by exact
root through `list_projects`, prefers graph, architecture, and trace operations
for discovery and impact analysis, and confirms consequential findings in
source and tests. `planner`, `explorer`, `architect`, and `database_architect`
may refresh one missing or stale index; other profiles fall back to ordinary
repository tools after one failed attempt. No profile loops on setup, and the
main/root coordinator must not use Codebase Memory to inspect the project.

Repository knowledge is an enforced worker context layer. Cortex adds
`docs/project/index.md` and `docs/features/index.md` to every briefing when
they exist. The planner selects task-relevant linked pages and recommends
their exact paths; the coordinator attaches them to future compact workers
through `context_files`. All workers re-check the indexes, treat documentation
as navigation and prior knowledge rather than authority, verify consequential
claims in source, tests, schemas, migrations, or executable configuration,
and persist `Knowledge reviewed:` evidence. Public report intake rejects a
missing index acknowledgement. Explicit context paths must be existing
project-relative regular files; absolute, traversing, missing, and symlink
paths are rejected.

Knowledge-harvest objectives force the canonical `plan`, `discover`,
`architecture`, `documentation`, `review`, and `close` pipeline. Incremental
harvest is valid only after a current source-backed coverage manifest proves a
zero-gap baseline. Otherwise the coordinator runs a full feature census, uses
2–8 non-overlapping domain explorers for a large repository, synthesizes stable
feature boundaries, writes behavior-complete pages, and requires an independent
review with zero unexplained unmapped surfaces. Refresh rebuilds the inventory
and also requires a no-change second documentation plan. Documentation,
review, and close reject a shallow feature index without Coverage matrix
columns, Inventory totals, Unmapped surfaces, Exclusions, or Known unknowns.

Reports are sanitized, task- and attempt-bound, and use one-use receipts.
Consuming a receipt writes an irreversible `reports/consumptions/` tombstone,
so reconciliation can repair derived receipts, indexes, and Markdown but
cannot replay consumed evidence. A report is capped at 64 KiB and 100 list
items per field; an attempt at 32 reports; a task at 256 reports and 1 MiB
total; and an attempt at 256 context grants. Every call includes an absolute
`project_root`; the same server process may serve multiple roots. Mutating v3
operations use server-owned request-digest receipts tied to the internal
active wave, so identical retries replay and changed or stale payloads
conflict before partial writes. Expected public v3 validation and recovery
outcomes return structured `ok: false` responses with bounded diagnostics and
a corrective `next_action`; because these are caller-correctable protocol
results, they do not enter the exception log. Exceptions raised at the MCP
boundary remain redacted and logged. Host model/tool/effort values
are selected routing metadata; v3 does not claim actual host attestation
unless the host supplies observable evidence.
Profiles and all 13 gate briefings are preloaded and validated at MCP startup;
invariant coverage checks that all 21 playbooks contain the required
professional sections and that every gate briefing has non-generic acceptance
and verification lists. Runtime validation also checks complete routing
metadata, TOML identity/description/sandbox parity, and unique implementation
specialist rules. Recovery and nested
operations are `inspect`, `resume`, `deactivate`, `lane`, `resource`, and
`question`.

Ledger, report-bus, and journal paths reject symlink ancestry and require
regular-file targets, so journal or report-bus links cannot redirect state
writes. Metrics reject negative token/elapsed values and non-finite or negative
costs; telemetry retains a bounded tail of 1,000 events or 512 KiB and records
evictions in `telemetry_dropped`.

Multi-agent v2 is required for explicit per-worker model selection. `explorer`
always selects Luna with coordinator-selected effort or a risk-based default;
Terra is only its host-unavailable fallback. The accepted effort vocabulary
ends at `max`. `planner` and all remaining non-security profiles default to Luna
at exactly `max`, while the coordinator may normally choose Terra from `medium`
through `max`. Luna `max` is the strong normal default, not a reason for
reflexive escalation. Security context, the security gate, and
`security_auditor` always select Sol with effort floors C1 `medium`, C2 `high`,
and C3 `xhigh`, capped at `max`. Non-security Sol requires matching
`user_requested_model` and `requested_model` from an explicit user choice; old
`sol_escalation`, auditable-extreme, failed-Terra, and model/effort-remap
authorization is rejected. Configured-default Luna omits native `model`, while
a hidden host-unavailable Terra fallback preserves selected effort.

Classification receipts are authoritative at initialization, so duplicate
complexity and requirements inputs are ignored. Host completion and gate proof
are separate: a passed attempt may be finalized before evidence linkage, while
the gate remains blocked until required evidence is recorded. A unique
context-grant id supplied where a report receipt is expected is corrected to
that report's one-use receipt. Other `commit_gate` validation failures are
recorded as bounded recovery events; after three failures for the same
gate/mode the task becomes `blocked` with an explicit handoff/resume action
instead of remaining active forever. Lanes support creation, leases, task
binding, resource claims, optional declared-worktree materialization,
reconciliation, and clean retirement; managed dirty worktrees are refused
during retirement.

## Verification

Run `python3 -m unittest discover -s tests -v`; the focused source-backed coverage is [test_cortex_control.py](../../../tests/test_cortex_control.py). Current 3.2.2 evidence includes 234 passing tests, skill quick validation for `knowledge-harvest`, `orchestrator`, and `cortex-control`, plugin and marketplace validation, Python compilation, shell syntax, installed and content-verified cachebuster `3.2.2+codex.20260814215722`, installer check/dry-run with the Luna default, cold boot, three deterministic Luna-high fixtures, the composite benchmark target, and the isolated fresh-plugin probe. The live model route, tracked release, and publication remain unverified. Related project commands are in [verification.md](../../project/verification.md).
<!-- GENERATED:END -->
