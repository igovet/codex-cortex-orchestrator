# Gotchas

<!-- GENERATED:START -->

## Ledger and references

- V12 state lives at
  `~/.codex/cortex/v12/projects/p-<project-hash>/cortex.db`; do not use the V11
  path or assume a database is inside the repository.
- `project_root` must resolve to the exact repository/worktree and is accepted
  only by `open_task`. Keep returned `handles.task_ref` for task-anchored
  task-anchored tools and canonical `task_id` as durable evidence. Different
  resolved roots intentionally get different ledgers.
  Do not infer a root from thread/MCP metadata, plugin `cwd`, or a hook.
- Optional `open_task.context` is bounded task metadata, not a project-root binding.
- Before the first project delegation, save the complete versioned task/result
  contract: exact `user_request_original`, `user_language`, English `objective`,
  independent outcomes with their linked acceptance criteria, and constraints.
  Do not silently overwrite the original request, copy acceptance into a
  standalone verification plan, or treat the contract as backend execution
  permission.
- Empty or placeholder outcomes are not complete. Each independent outcome needs
  a meaningful English requirement and its own meaningful acceptance criteria;
  optional constraints and context are not substitutes, and verification is not
  a fourth required obligation array.
- Do not edit SQLite, WAL, SHM, schema metadata, timeline sequences, or
  idempotency rows manually.
- Durable IDs, digests, and cursors are opaque references, not capabilities.
  Reuse them byte-for-byte from successful responses/inspections; never parse,
  concatenate, reconstruct, normalize, or append a suffix.
- An unresolved initiative dependency is intentionally storable as a warning.
  Do not treat an arbitrary unresolved ID as access to another project.

## Idempotency and concurrency

- Idempotency keys are scoped by operation in one project ledger. Reuse the same
  key only for the same normalized payload.
- `replayed=true` means the original mutation was returned; it is not a new
  event or proof that external work reran.
- `idempotency_conflict` is non-mutating. Generate a new key only after making
  a deliberate new write request.
- Concurrent writes are serialized by SQLite and receive ordered timeline
  sequences. Never infer semantic precedence from wall-clock completion alone.

## Delegations and reports

- `open_assignment` records work; it does not spawn a native worker or create
  host authority. Its successful response returns a host-neutral `dispatch_brief`
  and renderer/profile proof. Codex maps that semantic brief to the active host
  spawn operation. Do not create an ad-hoc prompt, use
  fewer workers than durable delegations, reuse one worker across delegations,
  or silently inherit model/effort/fork settings.
- Delegation `scope` is required non-empty text (maximum 65,536 characters) and
  should concisely name the worker's ownership boundary. Put execution detail
  in `instructions`; an object-shaped scope is a schema error.
- The server-rendered assignment bootstrap is the worker's authoritative brief and
  bounded chronology after host reconciliation. It is not needed after a
  healthy `open_assignment`, creates no receipt, and creates no predecessor
  barrier.
- Before delegation creation and native spawn, the six knowledge sections must
  appear exactly once, in order, and contain delegation-specific values. Missing,
  empty, TODO/TBD/unknown, or generic placeholder sections are invalid.
- Reports are immutable `progress`, `result`, `synthesis`, or `plan` evidence,
  not lifecycle completion or acceptance. Semantic publication owns storage and
  completion atomically, with one terminal outcome per delegation/report-kind slot.
  A plan has `informational` or
  `required` review policy. The coordinator owns the ordinary-chat review hold;
  the backend never makes it a gate.
- The owning native worker alone calls the applicable `publish_*` operation. The coordinator never
  fills in a missing plan, result, verification, synthesis, or documentation
  rationale; it follows up, reworks, or creates a parent-linked replacement.
- Report storage and replay identity are server-owned. Exact ambiguous publication
  retries replay; changed payloads conflict and require recovery/rework. Do not
  restart, append after finalization/abort, or overwrite immutable evidence.
- `read_task` is the bounded public evidence view. Workers begin with its
  server-rendered assignment view, and continue the same read only with the
  server-owned continuation; no report-reference or consumer-delegation fields
  are accepted by the public facade.
- Continue every inspection with `after_sequence` and only while
  while `has_more` using the returned `next_sequence`.
- A worker may end without a report. The coordinator can disclose the evidence
  gap and create a replacement; there is no backend recovery route.
- Native wait output and worker prose are ordinary model/host context, not
  ledger authority.

## Coordinator boundary

- The root coordinator orchestrates only. It may use Cortex ledger calls,
  native-agent coordination, user interaction, immutable reports, and the
  bounded orchestrator-owned knowledge route. It must not inspect/search
  source, edit, build, test, browse, or otherwise operate on target-project
  content.
- Activated Cortex skills are supplied by the host, not by the ledger MCP.
  Never call `read_mcp_resource`, `resources/read`, or a Cortex tool for a
  `skill://` URI.
- The bounded route uses the host-injected `AGENTS.md` context, then the project/feature indexes,
  and only task-relevant linked pages. The orchestrator alone owns its exact
  paths and six-part template: documents to consume first, applicable
  requirements, verification contract, ownership constraints, known
  documentation state, and further documentation discovery. Profiles consume
  the resulting per-delegation contract and never redo routing or broaden
  documentation discovery.
- The route is not shell or search authority. Coordinator reads require an
  already-known exact path and a non-shell direct reader. Delegate unknown
  roots/paths or unavailable direct reads; never use `rg`, `find`, globs,
  graph/source/repository search, directory listing, or candidate probes.
- Every project action and substantive domain analysis belongs to a worker,
  including discovery, planning grounded in files, implementation,
  documentation changes, review, and verification.
- Do not let a failed, missing, or contradictory report tempt the coordinator
  into a direct check. Create a focused follow-up, verifier, rework, specialist,
  or replacement delegation.
- Git, manifests, caches, worktrees, existence/absence or unchanged-state, and
  project-local `.codex` are project-state checks, not orchestration. Delegate
  them even when read-only, trivial, pre-plan, or explicitly user-requested.

## Governance and initiatives

- `minimal` is the default model choice for bounded low-risk work, not a
  backend default permission. Record and revise the mode explicitly when using
  Cortex.
- User override has priority. Do not silently rewrite it because the model
  assesses higher risk; record the warning and later evidence separately.
- Assessments append. A later row changes the projection without erasing history.
- Initiative status is informational. Every transition among the six allowed
  values is accepted; there is no transition graph.
- Unresolved/cyclic dependency warnings do not block status updates, task work,
  or initiative closure.
- `ready`, `ready_with_risks`, and `not_ready` are advisory recommendations.
  `not_ready` does not disable `open_assignment` or require a repair wave.
- Missing closure never blocks a final answer. Disclose material missing
  evidence rather than an internal ledger ceremony.
- After sufficient finalized worker evidence, the coordinator selects the
  verdict and automatically attempts the advisory write plus bounded
  inspection. `ready_with_risks` never asks for user confirmation. Do not
  conflate the independent `execution_outcome` with advisory bookkeeping.
- `read_task.execution_outcome` contains `evidence_status`,
  `finalized_report_count`, `completed_report_count`, `effective_revision`,
  `coverage_status`, and `outcome`. It derives deterministically from current
  effective-contract coverage, not report arrival order or historical claims;
  it is not a native-lifecycle claim. `advisory_closure` separately reports `record_status` and
  `latest_record`. Closure bookkeeping cannot change execution evidence.
- `close_task` returns `closure_confirmation` with
  `inspection_status`, `reason`, and `attempts`. The service allows at most one
  same-idempotency retry for a verified transient persistence or inspection
  failure. If confirmation remains `unconfirmed`, disclose that advisory
  limitation while retaining the independent `execution_outcome` evidence.
- `close_task` needs the exact task reference, one `verdict`, and bounded opaque
  JSON `evidence`; no subject or initiative locator is inferred. A durable
  `task_id` may appear in returned evidence, but is not a callable public
  locator. Omit optional risks/follow-ups to store empty lists.
- The public closure call has no subject digest or initiative fields; keep any
  private/internal initiative ledger bookkeeping out of the public request.
- The three narrow decision record operations are coordinator-asserted evidence. Use the matching advertised operation and preserve the exact user response.
  and preserve the exact ordinary-chat response in `response_original`; do not
  send retired `prompt_en`/`response_en` duplicate fields. Bind plan/report decisions to the exact
  immutable digest. Only plan `approve` also requires a current ready approval
  view and opaque handle. Plan `request_revision` and `cancel` deliberately do
  not use volatile view binding, so non-plan timeline events cannot block saved
  feedback. It neither authenticates the user nor grants authority.
- Ask a question only for a genuine product, requirement, scope, acceptance, or
  external/destructive authorization decision. Clarification, pause, plan
  revision, and cancellation are model-owned interaction policy, not a report,
  governance, initiative, or ledger failure gate.

## Conditional documentation stage

- After worker-owned project verification, assess documentation impact from
  reports before closure.
- Material behavior, architecture, interface, command, verification,
  convention, or feature-ownership changes require a documentation-sync worker
  and then a separate documentation-verifier worker.
- A no-impact task uses one finalized worker-owned report with an explicit
  English documentation-impact section and material/no-impact rationale, and
  creates no meaningless documentation edit. Use a bounded evidence-synthesis
  worker only when existing reports do not already contain that section; the
  coordinator never calls a worker-only `publish_*` operation or self-asserts the result.
- The final no-impact initiative must link the exact task, the exact
  documentation-impact `report_ref`, and every other required `report_ref`;
  closure evidence cites those compact refs and returned digests. Durable report
  IDs remain evidence only. A report-only initiative
  cannot surface reliably in task scope. Close the exact initiative, then verify
  task-scoped and initiative-scoped governance before claiming durable `ready`.
- Missing documentation update or verification evidence calls for rework,
  replacement, or explicit risk disclosure; it is never a backend gate.

## Profiles and model transport

- Profiles are advisory role templates. Do not treat profile selection as
  backend admission or a model-routing capability.
- Choose the exact packaged `profile_name` for every ordinary delegation and
  verify `profile_state=loaded` plus its digest. Keep the human-readable `role`
  separate; it is not profile proof. Use unavailable fallback only on a degraded
  non-durable dispatch with a complete explicit role contract and conspicuous
  limitation disclosure.
- Canonical recommendations use `high` for Luna, Terra, and Sol, but every model
  supports `low`, `medium`, `high`, `xhigh`, and `max`.
- Always preserve the selected effort and use `fork_turns="none"`.
- Omit the native `model` argument for logical Luna. Passing Luna explicitly
  violates the configured-default transport rule even though the logical audit
  metadata still records Luna.
- Pass exact Terra/Sol model overrides. Do not use a server-owned fallback or
  silently change the coordinator's pair.

## Installation and validation

- End users install/update through the README's GitHub Marketplace flow.
- Every live-dev test starts through `./scripts/cortex-dev`: it keeps the
  candidate's HOME, CODEX_HOME, plugin cache, config, and V12 state under the
  exact persistent `$HOME/.cortex-dev` boundary, prints the candidate target,
  and refreshes its cache/version before launching Codex. Reset requires
  `./scripts/cortex-dev-reset --confirm` and cannot target stable or arbitrary
  paths.
- `./scripts/sync-cortex.sh` is a local-source synchronization path, not a
  live-dev or public installation replacement; never use it to synchronize the
  user's real installed plugin.
- A source test does not prove the installed plugin or a live Codex session.
- Live acceptance uses ordinary interactive `codex` in a named background tmux
  session, never `codex exec`; create the session with `-d`, send the launcher
  and targeted input with `tmux send-keys`, capture a bounded pane, and clean
  up. Run `./scripts/cortex-dev` before the targeted input and record its
  printed isolated target and refreshed cache version. A terminal-permission
  prompt/denial or ordinary-Codex startup failure is failed or unverified from
  the bounded capture, never inferred as success.
- V12 has no lifecycle hooks or hook-trust flow. A hook prompt is a
  package/configuration mismatch, not an expected step.
- V11 databases are untouched and incompatible. Never migrate or adopt them for
  V12.
- Every native worker commentary, update, message, final response,
  tool-authored durable string, worker-authored durable record, and derived
  view source is English. Canonical product-facing report/handoff payloads may
  carry one optional unchanged `source_text` value as inert source material;
  task and decision contracts retain user text in explicit `*_original` fields.
  Localize coordinator-to-user summaries,
  questions, decisions, and ready-view explanations.
- The database is canonical; only plan and finalized-report Markdown views are
  host-private derived files under the V12 shard. Task, decision, delegation,
  initiative, closure, governance, handoff, index, and timeline data remain in
  SQLite. Never write a database, report, decision, projection, or project-local
  `.codex` state under `project_root`.
- Publish a plan/report-view link only from a current `ready` tool response using the
  exact returned absolute path, with a localized summary and next step. `stale`,
  `conflict`, `unavailable`, and `disabled` have no safe link; summarize the
  canonical evidence instead. See [human-readable task views](../features/human-readable-task-views/index.md).
- Documentation is navigation. Confirm consequential claims in source, schemas,
  bundled skills, executable configuration, and tests.

## Operator maintenance

- `cortex_runtime.v12_maintenance` is not a twelfth MCP tool. Run it only as a
  deliberate local administrator action, anchored to an exact retained V12
  `task_id`.
- A backup covers the entire project shard even though one task anchors the
  command. Treat every sealed backup as potentially containing every task,
  report, plan, decision, and governance row in that shard.
- Restore is offline only. `--confirm-service-stopped MCP_STOPPED` merely
  asserts that the operator already stopped all normal MCP processes; it does
  not acquire a lock or make a live restore safe.
- Projection prune and backup retention default to dry-run. Never bypass exact
  confirmation tokens, apply with unsafe candidates, delete a whole directory
  recursively, or treat maintenance cleanup as canonical ledger retention.
- Maintenance accepts no `project_root`, custom database/export path, or V11
  target, emits sanitized JSON, and writes no project file.
- See [operator maintenance](../features/operator-maintenance/index.md) for the
  exact command and confirmation spelling.

<!-- GENERATED:END -->
