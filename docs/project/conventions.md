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
  URI; the twenty-tool registry has no skill-resource reader.
- Treat Cortex as a durable coordination ledger. The coordinator owns only a
  model-held dynamic orchestration DAG and governance; the backend owns storage
  integrity, immutable evidence, bounded reads, and derived host-private
  projections. The coordinator never authors a project solution plan.
- For light/full delivery, and whenever minimal work otherwise needs planning,
  delegate it to `planner`; the planner worker submits
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
  further-discovery boundary into each delegation's `instructions`. The native
  brief is only compact bootstrap context; profiles consume the full supplied
  contract after the mandatory first assignment read and never redo routing,
  reconstruct the template, or repeat the index-path list.
- Before `open_assignment` or native spawn, require those six labels exactly
  once, in order, with non-empty delegation-specific content. Empty, omitted,
  TODO/TBD/unknown, or generic placeholder sections are invalid.
- Keep the public facade at exactly the twenty canonical action-specific tools in
  `public_contracts.py`. Coordinator and worker projections are audience-specific.
- Let the active MCP schema own exact fields, types, sizes, enumerations, and
  response shapes. Documentation mirrors semantics and the exact tool names but
  is not validator input.
- Supply the exact resolved repository/worktree as `project_root` only to
  `open_task`; retain `handles.task_ref` for the task-anchored calls.
  The returned canonical `task_id` is non-callable durable evidence. Never copy
  a UI-rendered task ID into a public task call or infer a root from MCP metadata, thread identity, the
  plugin process `cwd`, hooks, or project/database directory scanning.
- Create a versioned task/result contract before the first project delegation:
  preserve `user_request_original` and `user_language`; store an English
  `objective`; and state independent outcomes, their linked acceptance
  criteria, and constraints. Do not silently improve, discard, or overwrite the
  original request. Treat optional `open_task.context` as bounded task metadata,
  never as a root binding.
- Before `open_task`, require every independent outcome to have a meaningful
  requirement and linked acceptance criteria. Keep constraints as linked task
  metadata and keep the independent verification plan empty at creation rather
  than deriving it from acceptance. Optional context cannot replace an outcome;
  record a bounded assumption against the affected outcome instead of an
  unknown or placeholder contract entry.
- Keep delegation `scope` a required non-empty text boundary of worker ownership;
  put detailed execution in `instructions` and reject object scope.
- Call `close_task` with the exact task reference, one `verdict`, and bounded
  closure `evidence`; optional risk, follow-up, and completion-note fields are
  task-scoped. The public request has no subject, initiative, or closure-digest
  field. Durable `task_id` values are evidence only.
- After sufficient finalized worker evidence, select `ready`,
  `ready_with_risks`, or `not_ready`, then let the closure call automatically
  attempt the advisory write and inspect the intended record. `ready_with_risks`
  never requires user confirmation. Keep the independent `execution_outcome`
  projection separate from `advisory_closure` bookkeeping.
- Use `read_state` only to choose the next action, then select `read_scope`,
  `read_outcome`, `read_continuations`, `read_evidence`, or newest-first
  `read_timeline` only for the required detail. Worker-only `read_task` consumes
  the immutable assignment. A bounded response may set `has_more`; continue
  only the immediately preceding same read with `continue=true`. A closure
  cannot change the underlying evidence. If a
  closure confirmation is unavailable, disclose advisory uncertainty without
  changing the evidence projection.
- On coordinator recovery or compaction, read scalar state once. If it shows
  active or unfinished delegated work, use `read_continuations` next;
  `read_timeline` is only for an explicit chronology or audit request.
- Reuse the exact Cortex-issued `task_ref` byte-for-byte. Durable `*_id`, digest,
  and private/internal cursor values are opaque non-callable ledger evidence, not bearer
  capabilities. Never parse, concatenate, reconstruct, normalize, reformat, or
  append a suffix to the public task reference.
- Public mutation replay identity is derived privately from task/assignment
  context and canonical content; callers do not submit replay keys.
- Treat publications as immutable evidence. `open_assignment.report_policy` selects
  the relevant finalized predecessor evidence, and a fresh worker consumes the
  server-rendered assignment view as its first Cortex call; coordinator reads
  never substitute for that bootstrap.
- The owning native worker alone calls the applicable `publish_plan`,
  `publish_result`, or `publish_documentation` operation for its plan, result,
  verification, synthesis, or documentation-impact evidence. Its completion
  handoff returns a concise summary; the coordinator consumes the bounded
  server-produced evidence through `read_evidence`. A downstream worker
  receives finalized evidence only when its assignment report policy selects
  it. A report gap leads to follow-up, rework, or replacement.
- Private/internal storage may retain immutable report types `progress`, `result`,
  `synthesis`, and `plan`; public workers use the three matching publication
  operations.
  A plan's review policy is `informational` or `required`; review is a
  coordinator-owned ordinary-chat hold. Whenever the current plan policy is
  `required`, backend admission also requires explicit approval of the exact
  current finalized plan; silence, unrelated text, and approval of an older
  revision do not pass.
- Use the semantic publication operation for one complete terminal publication outcome.
  The server owns storage and derives replay identity from the delegation,
  phase, assembly state, and canonical payload. Exact ambiguous retries replay;
  changed payloads conflict and require an explicit recovery/rework delegation.
  Never restart, overwrite, or publish after terminal completion. Use an explicit
  superseding publication for replacement.
- Preserve the narrow read boundaries. When `has_more` is true, continue only
  the immediately preceding same operation with `continue=true`; the server
  retains position privately. A worker may read only its immutable assignment
  and the predecessor evidence selected for it.
- Use the matching narrow decision record operation only when coordinator policy has identified an
  ordinary-chat response as a direct user decision. Use neutral `prompt`, append
  the exact original-language response in `response_original`, and retain no
  translated duplicate fields. The server privately binds plan/publication
  decisions to the relevant canonical evidence. Only plan `approve`
  additionally checks a current private ready approval view; plan
  `request_revision` and `cancel` retain the relevant plan evidence without a
  public volatile binding. The attribution is evidence, not
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
- Do not add a server-owned model fallback, fresh-connection worker-authority
  recovery, wave/gate state machine, or server-selected native lifecycle.
  Preserve the digest-only supported-host audience receipt, monotonic MCP
  connection roles, explicit blocked/aborted successor lineage, and the narrow
  pre-dispatch assessment plus exact required-review plan-approval admission
  invariants; none schedules project work.
- The activation/lifecycle hooks bind native dispatch and enforce the
  coordinator/worker capability boundary. Native wait/stop state remains outside
  the ledger and is never a prerequisite for report access or completion.
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
  worker-owned publication with an explicit English documentation-impact section and
  material/no-impact rationale, and make no meaningless documentation edit. Use
  a bounded evidence-synthesis/documentation-impact worker when existing
  publications do not already contain that section. The coordinator may use the
  bounded routing exception to identify affected knowledge paths, but it neither
  edits nor verifies the documents, never calls a worker-only `publish_*` operation, and bases
  impact on worker publications. Missing documentation evidence leads to model-owned
  rework, replacement, or risk disclosure, never a backend gate.
- For that no-impact close, confirm through `read_task` that the finalized
  documentation-impact publication and every required implementation and
  verification result are present in current task coverage. Never accept a
  self-asserted `documentation_not_required` or a durable `ready` claim before
  the task closure write succeeds.
- Store V12 state only in the new schema-v1 namespace at
  `~/.codex/cortex/v12/projects/p-<project-hash>/cortex.db`. Never open, migrate,
  delete, or modify V11 databases.
- Treat WAL/SHM files as SQLite machinery rather than evidence. Never repair a
  ledger by editing database files or derived content manually.
- The database is canonical. Only plan and finalized-publication Markdown views are
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
