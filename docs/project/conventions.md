# Conventions

<!-- GENERATED:START -->

- Installable behavior lives below [plugins/cortex](../../plugins/cortex/).
  Repository-only documents and `AGENTS.md` do not substitute for bundled
  skills, runtime code, executable configuration, or tests.
- Activate Cortex only when the user explicitly selects or mentions
  `cortex:orchestrator`. Never infer activation from task complexity or
  repository state.
- Treat activated bundled skill text as host-supplied context. Never call
  `read_mcp_resource`, `resources/read`, or a Cortex tool to fetch a `skill://`
  URI; the fifteen-tool registry has no skill-resource reader.
- Treat Cortex as a durable coordination ledger. The coordinator owns only a
  model-held dynamic orchestration DAG and governance; the backend owns storage
  integrity, immutable evidence, bounded reads, and derived host-private
  projections. The coordinator never authors a project solution plan.
- When planning is needed, delegate it to `planner`; the planner worker submits
  the durable immutable `plan` report, and every plan-dependent worker names it
  as a predecessor. Evidence may add, remove, reorder, retry, or parent-link
  rework unstarted DAG nodes without changing completed reports. Persist each
  DAG revision through the task-linked initiative and delegation/report/decision
  graph; it is advisory history, not a backend-executed workflow.
- A requested or necessary main plan requires a fresh verified host-private
  Markdown link and explicit localized approve/revise/reject/cancel input.
  Record approval against its exact report/digest, then pass that compact
  decision ref in `input_decision_refs` to every plan-dependent delegation. Do
  not start implementation or research
  beyond discovery/planning first. A C1 skip is valid only without a user plan
  request and with explicit rationale.
- Retain C1/C2/C3 as advisory baselines: bounded low-risk work / multi-step or
  cross-surface work / high-risk or cross-domain work, normally `minimal` /
  `light` / `full` governance. They are not backend waves, mandatory gates,
  automatic model routing, or user-approval gates; evidence and explicit user
  preferences may revise them.
- Keep the root coordinator strictly orchestration-only. It may define outcome
  and acceptance, choose/revise governance, create/inspect ledger records,
  delegate and coordinate native workers, read reports, decide rework, record
  advisory closure, and synthesize the final answer.
- Preserve the coordinator's sole project-read exception: bounded knowledge
  routing through the host-injected `AGENTS.md` context, `docs/project/index.md`,
  `docs/features/index.md`, and only task-relevant pages selected from those
  indexes. The bundled orchestrator skill alone owns the exact route and
  six-part per-delegation template: documents to consume first, applicable
  requirements, verification contract, ownership constraints, known
  documentation state, and further documentation discovery.
- Treat that exception as a closed direct-read allowlist. The coordinator uses
  a non-shell direct reader only after the exact path is already known. It never
  uses shell/commands, `rg`, `find`, globs, graph/source/repository search,
  directory listing, or candidate-path probes for routing; unknown roots or
  paths require a worker.
- Delegate every project discovery, source/code/configuration read or search,
  arbitrary documentation discovery, substantive domain analysis, edit,
  command, build, test, browser check, review, and verification action. The
  coordinator must never use a project-facing tool to fill a report gap itself.
- Treat root discovery and Git/manifests/caches/worktrees, existence/absence or
  unchanged-state, and project-local `.codex` as project-state verification.
  Always delegate those checks, including before plan review, after a missing
  report, or when the user explicitly asks the coordinator to check them.
- Compile the selected documents, applicable requirements, verification
  contract, ownership constraints, known documentation state, and explicit
  further-discovery boundary into each delegation's `instructions` and native
  brief. Profiles consume this supplied contract and never redo routing,
  reconstruct the template, or repeat the index-path list.
- Before `create_delegation` or native spawn, require those six labels exactly
  once, in order, with non-empty delegation-specific content. Empty, omitted,
  TODO/TBD/unknown, or generic placeholder sections are invalid.
- Keep the public facade at exactly the fifteen canonical action-specific tools in
  `public_contracts.py`. Coordinator and worker catalogs are identical.
- Let the active MCP schema own exact fields, types, sizes, enumerations, and
  response shapes. Documentation mirrors semantics and the exact tool names but
  is not validator input.
- Supply the exact resolved repository/worktree as `project_root` only to
  `create_task`; retain `handles.task_ref` for the seven task-anchored calls.
  The returned canonical `task_id` is non-callable durable evidence. Never copy
  a UI-rendered task ID into a public task call or infer a root from MCP metadata, thread identity, the
  plugin process `cwd`, hooks, or project/database directory scanning.
- Create a versioned task/result contract before the first project delegation:
  preserve `user_request_original` and `user_language`; store an English
  `objective`; and state independent outcomes, their linked acceptance
  criteria, and constraints. Do not silently improve, discard, or overwrite the
  original request. Treat optional `create_task.context` as arbitrary task JSON,
  never as a root binding.
- Before `create_task`, require every independent outcome to have a meaningful
  requirement and linked acceptance criteria. Keep constraints as linked task
  metadata and keep the independent verification plan empty at creation rather
  than deriving it from acceptance. Optional context cannot replace an outcome;
  record a bounded assumption against the affected outcome instead of an
  unknown or placeholder contract entry.
- Keep delegation `scope` a required non-empty text boundary of worker ownership;
  put detailed execution in `instructions` and reject object scope.
- Call `submit_governance_closure` with required `subject_type`, the exact
  existing compact `subject_ref`, one `verdict`, and bounded opaque JSON
  `evidence`. A task subject uses the exact anchored `task_ref` as
  `subject_ref` and omits initiative-only `initiative_status`; opaque
  `completion_notes` are valid for either subject. An initiative subject uses
  the exact returned `initiative_ref` as `subject_ref`.
  Durable `subject_id` values are evidence only. Never invent a closure digest
  field.
- After sufficient finalized worker evidence, select `ready`,
  `ready_with_risks`, or `not_ready`, then let the closure call automatically
  attempt the advisory write and inspect the intended record. `ready_with_risks`
  never requires user confirmation. Keep the independent `execution_outcome`
  projection separate from `advisory_closure` bookkeeping.
- `inspect_task` exposes the exact four-field `execution_outcome`:
  `evidence_status`, `finalized_report_count`, `completed_report_count`, and
  `outcome`. The finalized count covers every finalized report; the completed
  count covers semantically valid canonical finalized results with status
  `completed`. `outcome` is `null` before the first semantically valid canonical
  finalized result and then reflects the latest such result as `completed` or
  `incomplete`, without claiming native lifecycle. It exposes
  `advisory_closure` with `record_status` and `latest_record`. A closure cannot
  change execution evidence. `submit_governance_closure` returns
  `closure_confirmation` with `inspection_status`, `reason`, and `attempts`;
  only one same-idempotency retry is allowed for a verified transient
  persistence or inspection failure. If the result is `unconfirmed`, disclose
  advisory uncertainty without changing the evidence projection.
- Reuse returned compact task/entity refs and every digest/cursor byte-for-byte.
  Durable `*_id` values are opaque non-callable evidence, not bearer
  capabilities. Never parse, concatenate, reconstruct, normalize, reformat, or
  append a suffix to any ref or ID.
- Treat the `task_ref` on initiative operations only as the locator for the
  saved project ledger, never as governance permission or a lifecycle gate.
- Every mutation requires a caller-generated operation-scoped idempotency key.
  Reuse that exact key unchanged only for an
  exact normalized-payload retry; the same payload replays the original result
  and conflicting content returns a non-mutating `idempotency_conflict`.
- Treat reports as immutable evidence. Pass only finalized relevant report refs
  and their exact manifest digests to later delegations. Worker consumption is
  evidenced by a `read_reports` call with the consuming worker's own exact
  `consumer_delegation_ref` (the delegation must declare the input), plus its receipt;
  coordinator reads never substitute for that evidence.
- The owning native worker alone calls `submit_report` for its plan, result,
  verification, synthesis, or documentation-impact evidence. Its completion
  handoff returns a concise `Summary` and exact `Report ref`; the coordinator
  consumes that handoff and does not reread the body merely to summarize it. A
  downstream worker reads the finalized report only when its declared work
  requires the body. A report gap leads to follow-up, rework, or replacement.
- Use the immutable report types `progress`, `result`, `synthesis`, and `plan`.
  A plan's review policy is `informational` or `required`; review is a
  coordinator-owned ordinary-chat hold, never a backend gate. A required review
  needs an explicit unambiguous response to that exact finalized plan digest;
  silence and unrelated text are not approval.
- Use the semantic publication operation for one complete terminal report outcome.
  The server owns storage and derives replay identity from the delegation,
  phase, assembly state, and canonical payload. Exact ambiguous retries replay;
  changed payloads conflict and require an explicit recovery/rework delegation.
  Never restart, overwrite, or publish after terminal completion. Use an explicit
  superseding report for replacement.
- Preserve `read_reports` request order and its 20-report maximum; select only
  needed sections, use compact `report_refs`, and continue through its fixed 65,536-byte server pages
  with the returned cursor until complete. Omitting a consuming delegation is metadata-only
  recovery. A worker must name its
  own delegation and may read only declared
  finalized inputs; retain the exact receipt digest, chunk indexes, and cursor
  transitions. Continue task/delegation/governance inspection with `after_sequence`
  and preserve `next_sequence`/`has_more`; pages are fixed at 50 events.
- Use the matching narrow decision record operation only when coordinator policy has identified an
  ordinary-chat response as a direct user decision. Use neutral `prompt`, append
  the exact original-language response in `response_original`, and retain no
  `prompt_en`/`response_en` duplicate fields. Bind
  plan/report decisions to the exact canonical digest. Only plan `approve`
  additionally binds a current ready approval view and opaque approval handle;
  plan `request_revision` and `cancel` retain the exact plan digest/response
  without a volatile view binding. The attribution is evidence, not
  authentication, authority, a bearer token, or a workflow gate.
- Ask a localized question only for a genuine product, requirement, scope,
  acceptance, or external/destructive-authorization decision, then end the
  turn. Clarification, plan review, pause, revision, and cancellation are
  coordinator-owned interaction policy; ledger, report, worker, initiative, or
  closure state must never manufacture a user hold.
- Select `minimal`, `light`, or `full` based on the task and revise the mode when
  new evidence changes the appropriate depth. An explicit user override wins.
- Never make a governance record an admission rule. Missing closure,
  `not_ready`, open initiatives, unfinished links, missing reports, and
  unresolved/cyclic dependencies do not block safe coordination or a final
  answer.
- Create a project initiative only for a shared long-lived goal, risk,
  milestone, dependency, or cross-task assessment. The model owns status,
  dependencies, links, and closure interpretation.
- Accept any transition among `proposed`, `active`, `paused`, `completed`,
  `closed`, and `cancelled`. Preserve unresolved/cyclic dependency warnings.
- Treat `ready`, `ready_with_risks`, and `not_ready` as coordinator-selected
  advisory verdicts, never execution outcomes or backend lifecycle states.
- Keep agent profiles advisory. Profiles describe roles and quality bars but do
  not select models, pin efforts, authorize tools, or enforce capabilities.
- Select one exact packaged `profile_name` for every ordinary delegation and
  require loaded renderer proof plus digest. Keep `role` as a separate bounded
  human-readable assignment label; it is not profile selection. Unavailable
  fallback is limited to degraded non-durable dispatch and requires a complete
  explicit role contract plus visible disclosure.
- Choose one exact model/effort pair per delegation. Canonical recommendations
  are `high` for Luna, Terra, and Sol; all support `low`, `medium`, `high`,
  `xhigh`, and `max`.
- Route Luna first, including Explorer and ordinary discovery; increase Luna
  effort before changing models. Terra is for evidence-backed genuinely complex
  non-security work or planning, while Sol is for security work and review.
- For native dispatch use `fork_turns="none"` and preserve the exact effort.
  Omit the native `model` argument for logical Luna because it is the configured
  default; pass exact Terra or Sol overrides.
- Map each successful durable delegation to exactly one native host spawn. The
  first worker with a profile uses its exact profile name; same-profile
  siblings use the server-issued `_2`, `_3`, and later numeric suffixes. Map
  the returned host-neutral `dispatch_brief` to the active host operation,
  preserving its exact rendered message and effort semantics; never assemble an
  ad-hoc prompt, omit a spawn, dispatch twice, or reuse one worker for multiple
  delegations. Reconcile an ambiguous host result by exact native handle before
  replacement.
- Keep the standard Codex To-Do projection limited to current pipeline stages
  and review state. Refresh it whenever either changes; never use it for worker
  subtasks, implementation checklists, or report-body copies. Concise handoff
  summaries carry current stage/state, outcome, next owner/action, pipeline or
  review delta, changed/verified surface, exact report ref/digest, and residual
  risk or unrun checks so routine coordinator body reads are unnecessary.
- Treat planner-authored implementation microtasks as evidence for the
  model-owned orchestration DAG only. They are not backend jobs, scheduling
  instructions, worker-subtask checklists, or report-body copies.
- Do not add a server-owned model fallback, worker recovery route, wave/gate
  state machine, plan-approval gate, receipt-gated lifecycle, host binding, or
  lifecycle authorization. Coordinator-owned plan review and worker handoff
  delivery receipts remain valid advisory/evidence practices.
- V12 ships no lifecycle hooks. Native spawn/wait/stop state is outside
  the ledger and never a prerequisite for report access or completion.
- Keep every native worker commentary/update, inter-worker message, final
  response, tool-authored durable string, report, decision normalization,
  ledger prose, and durable view source content in English. Acceptance scans
  complete child threads, not only final messages or database rows. Preserve
  user text only in explicitly named `*_original` fields.
  Localize coordinator-to-user questions, summaries, progress, decision notices,
  ready-view explanations, and final handoffs to the latest meaningful user
  language unless the user requests another language.
- After project verification is reported, assess documentation impact before
  closure. Material behavior, architecture, interface, command, verification,
  convention, or feature-ownership changes require a documentation-sync worker
  to update harvest documentation under `docs/project/` and `docs/features/`,
  followed by a separate delegated documentation verification.
- When there is no material documentation impact, require one finalized
  worker-owned report with an explicit English documentation-impact section and
  material/no-impact rationale, and make no meaningless documentation edit. Use
  a bounded evidence-synthesis/documentation-impact worker when existing
  reports do not already contain that section. The coordinator may use the
  bounded routing exception to identify affected knowledge paths, but it neither
  edits nor verifies the documents, never calls `submit_report`, and bases
  impact on worker reports. Missing documentation evidence leads to model-owned
  rework, replacement, or risk disclosure, never a backend gate.
- For that no-impact close, create/update an initiative with the exact task
  relationship, the exact documentation-impact `report_ref`, and every other
  required finalized report link. Cite the exact compact report refs and
  returned digests in closure evidence, close the exact returned initiative,
  then inspect
  governance in task scope and initiative scope. Never accept a self-asserted
  `documentation_not_required`, a report-only final initiative, or a durable
  `ready` claim before the closure write and both inspections agree.
- Store V12 state only in the new schema-v1 namespace at
  `~/.codex/cortex/v12/projects/p-<project-hash>/cortex.db`. Never open, migrate,
  delete, or modify V11 databases.
- Treat WAL/SHM files as SQLite machinery rather than evidence. Never repair a
  ledger by editing database files or derived content manually.
- The database is canonical. Only plan and finalized-report Markdown views are
  derived host-private files beside the V12 shard; task, decision, delegation,
  initiative, closure, governance, handoff, index, and timeline data remain in
  SQLite. Never use a view as a recovery input or worker prompt. Never write a
  database, projection, report, decision, or project-local `.codex` state under
  `project_root`.
- Publish a human-view path only when the current tool response reports `ready`
  with its exact absolute path after containment, regular-file, digest, and
  source-sequence verification. Make it a clickable link and pair it with a
  localized summary and its effect or next step. `stale`, `conflict`,
  `unavailable`, and `disabled` expose no link and do not block canonical
  evidence or an honest final answer.
- Keep `cortex_runtime.v12_maintenance` outside the MCP catalog. Anchor every
  operator command to one existing `task_id`; never add `project_root`, an
  arbitrary database/export path, V11 lookup, or orchestration authority.
- Treat maintenance backup as a sealed whole-project-shard backup anchored to a
  task, not a task-only export. Require exact uppercase confirmations for every
  write. Projection prune and backup retention remain dry-run by default and
  never delete canonical rows.
- Restore is strictly offline. Stop and independently verify all normal Cortex
  MCP access before supplying `RESTORE`, exact task/shard identity, and
  `MCP_STOPPED`; the acknowledgement is not a cross-process lock.
- Keep secrets, credentials, personal data, raw reports, prompts, worker
  transcripts, and private diagnostics out of documentation, fixtures, issues,
  commits, and logs.
- Keep end-user install/update on the README's GitHub Marketplace flow. Every
  interactive repository live-dev test starts through `./scripts/cortex-dev`:
  it isolates `HOME` and `CODEX_HOME` under the exact persistent
  `$HOME/.cortex-dev` candidate directory, refreshes the candidate cache/version
  there, and only then launches Codex. Use `./scripts/cortex-dev-reset --confirm`
  only to remove that dedicated candidate; its path guards must never be
  weakened. Direct `./scripts/sync-cortex.sh` is not a live-dev mechanism and
  must never synchronize the user's real installed plugin; `--dry-run`/`--check`
  are read-only validation modes.
- Delegate the smallest non-destructive checks that prove affected behavior,
  broaden in proportion to risk, and report every unrun release or live-host
  check.
- Live tests use ordinary interactive Codex inside a named background `tmux`
  session only, never `codex exec`; create the session with `-d`, send the
  launcher and targeted input with `tmux send-keys`, capture a bounded pane,
  and clean up. Run `./scripts/cortex-dev` before the targeted input, verify
  and record its printed isolated `HOME`/`CODEX_HOME` target and refreshed cache
  version, then keep the smoke narrow and record the exact command and outcome.
  A terminal-permission prompt/denial or ordinary-Codex startup failure is
  failed or unverified from the bounded capture, never inferred as success.
- After behavior, interface, command, diagram, or version changes, delegate a
  documentation verifier to re-read README, SECURITY, release readiness, and
  all affected Markdown and validate links, Mermaid syntax, tool names, paths,
  versions, and documented commands.
- Source, tests, schemas, bundled skills, and executable configuration are
  authoritative when documentation drifts.

<!-- GENERATED:END -->
