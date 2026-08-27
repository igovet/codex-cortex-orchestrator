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
  URI; the eleven-tool registry has no skill-resource reader.
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
  Record approval against its exact report/digest, then pass that decision ID to
  every plan-dependent delegation. Do not start implementation or research
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
  routing through every applicable `AGENTS.md`, `docs/project/index.md`,
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
- Keep the public facade at exactly the eleven canonical action-specific tools in
  `public_contracts.py`. Coordinator and worker catalogs are identical.
- Let the active MCP schema own exact fields, types, sizes, enumerations, and
  response shapes. Documentation mirrors semantics and the exact tool names but
  is not validator input.
- Supply the exact resolved repository/worktree as `project_root` only to
  `create_task`; retain `handles.task_ref` for the seven task-anchored calls
  and canonical `task_id` as durable evidence. Never copy a UI-rendered task
  ID into a public task call or infer a root from MCP metadata, thread identity, the
  plugin process `cwd`, hooks, or project/database directory scanning.
- Create a versioned task/result contract before the first project delegation:
  preserve `user_request_original` and `user_language`; store an English
  `objective`; and state requirements, constraints, acceptance criteria, and a
  verification plan. Do not silently improve, discard, or overwrite the
  original request. Treat optional `create_task.context` as arbitrary task JSON,
  never as a root binding.
- Before `create_task`, require non-empty meaningful `requirements`,
  `constraints`, `acceptance_criteria`, and `verification_plan` arrays. Optional
  context cannot replace them; record a bounded assumption and verification
  item instead of an unknown/placeholder array.
- Keep delegation `scope` a required non-empty text boundary of worker ownership;
  put detailed execution in `instructions` and reject object scope.
- Call `submit_governance_closure` with required `subject_type`, the exact
  existing `subject_id`, one `verdict`, and bounded opaque JSON `evidence`.
  A task subject uses the exact anchored task ID and omits initiative-only
  status/completion fields; an initiative subject uses the exact returned ID
  related to the anchored task. Never invent a closure digest field.
- Reuse returned task, delegation, report, initiative, assessment, and closure
  IDs and every digest/cursor byte-for-byte. They are opaque durable references,
  not bearer capabilities: never parse, concatenate, reconstruct, normalize,
  reformat, or append a suffix.
- Treat the `task_ref` on initiative operations only as the locator for the
  saved project ledger, never as governance permission or a lifecycle gate.
- Use idempotency keys for retried writes. Same normalized payload returns the
  original record; conflicting content must fail without mutation.
- Treat reports as immutable evidence. Pass only finalized relevant report IDs
  and their exact manifest digests to later delegations. Worker consumption is
  evidenced only by `read_reports(reader_kind="worker", consumer_delegation_id=…)`
  receipts; coordinator reads never substitute for that evidence.
- The owning native worker alone calls `submit_report` for its plan, result,
  verification, synthesis, or documentation-impact evidence. The coordinator
  creates/dispatches the delegation, waits, and reads its finalized report; a
  report gap leads to follow-up, rework, or parent-linked replacement.
- Use the immutable report types `progress`, `result`, `synthesis`, and `plan`.
  A plan's review policy is `informational` or `required`; review is a
  coordinator-owned ordinary-chat hold, never a backend gate. A required review
  needs an explicit unambiguous response to that exact finalized plan digest;
  silence and unrelated text are not approval.
- Use `single`, `begin`, sequential `append`, `finalize`, and `abort` report
  modes. A single report is at most 65,536 bytes; each appended chunk is at most
  32,768 bytes; a report has at most 256 chunks and 8 MiB total. Resume an
  interrupted assembly from its manifest and `next_chunk_index`; never restart,
  skip, overwrite, or append after finalization/abort. Use an explicit
  superseding report for replacement.
- Preserve `read_reports` request order and its 20-report maximum; select only
  needed sections, respect its maximum 65,536-byte content budget, and continue
  with the returned cursor until complete. `max_bytes=0` is metadata-only
  recovery. A worker must name its own delegation and may read only declared
  finalized inputs; retain the exact receipt digest, chunk indexes, and cursor
  transitions. Bound task/delegation/governance inspection with `after_sequence`
  plus `limit` and preserve `next_sequence`/`has_more`.
- Use `record_user_decision` only when coordinator policy has identified an
  ordinary-chat response as a direct user decision. Append the exact response in
  `response_original`, retain `response_en` as English normalization, and bind
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
- Treat `ready`, `ready_with_risks`, and `not_ready` as model-authored advisory
  verdicts, never backend lifecycle outcomes.
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
- For native dispatch use `fork_turns="none"` and preserve the exact effort.
  Omit the native `model` argument for logical Luna because it is the configured
  default; pass exact Terra or Sol overrides.
- Map each successful durable delegation to exactly one native host spawn. The
  first worker with a profile uses its exact profile name; same-profile
  siblings use the server-issued `_2`, `_3`, and later numeric suffixes. Copy
  the returned native arguments byte-for-byte, including the exact rendered
  message and explicit effort; never assemble an ad-hoc prompt, omit a spawn,
  dispatch twice, or reuse one worker for multiple delegations. Reconcile an
  ambiguous host result by exact native handle before replacement.
- Do not add a server-owned model fallback, worker recovery route, wave/gate
  state machine, plan approval, receipt protocol, host binding, or lifecycle
  authorization.
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
  relationship, the exact documentation-impact report ID, and every other
  required finalized report link. Cite the exact report IDs and returned digests
  in closure evidence, close the exact returned initiative, then inspect
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
- Keep end-user install/update on the README's GitHub Marketplace flow. Use
  `./scripts/sync-cortex.sh` only for explicitly authorized repository
  source synchronization; `--dry-run`/`--check` are validation modes.
- Delegate the smallest non-destructive checks that prove affected behavior,
  broaden in proportion to risk, and report every unrun release or live-host
  check.
- After behavior, interface, command, diagram, or version changes, delegate a
  documentation verifier to re-read README, SECURITY, release readiness, and
  all affected Markdown and validate links, Mermaid syntax, tool names, paths,
  versions, and documented commands.
- Source, tests, schemas, bundled skills, and executable configuration are
  authoritative when documentation drifts.

<!-- GENERATED:END -->
