# Worker routing

## Model and effort

Retain the user's selected coordinator model and effort. Do not propagate that cost
to every worker.

Apply this mandatory worker-model policy to every subagent assignment. Put model
and effort in the native request and repeat them in the assignment/pipeline; do
not rely on an inherited host default.

- Fact gathering, exploration, source/log reading and known checks use
  `gpt-5.6-luna` at `medium` or `high`, including in security-related projects.
  Ordinary implementation, documentation and plans of already-known steps also
  use Luna. Do not create a planner just to restate a small task.
- Coupled implementation or nontrivial ordinary review uses `gpt-5.6-terra` at
  `medium` or `high`. State the concrete complexity; not every multi-file edit
  warrants Terra.
- Consequential decisions use `gpt-5.6-sol` at `medium` (`decision`): architectural
  boundaries, public contracts, migration strategy or a plan with substantial
  risk. Use `high` (`decision-hard`) for conflicting requirements, difficult
  rollback, data-loss risk or security-boundary decisions. A narrow security audit
  may use Sol `medium`/`high`; implementation returns to Luna/Terra.
- Exceptional system decisions use `gpt-6-astra` only at `medium`
  (`decision-exceptional`): verified facts still support competing consequential
  designs, with a stated reason Sol is insufficient. This is not an automatic
  escalation after a failed call, timeout or review.
- `planner`, `architect`, `database_architect` and similar profiles follow the
  decision's risk, not the role name. Sol/Astra consume bounded evidence gathered
  by Luna; they do not replace exploration. A missing fact goes back to its owner.
- Opt-in `senior_consultant` uses the same decision ladder: Sol `medium` standard
  or narrow, Sol `high` harder, Astra `medium` exceptional. Consultation stays
  bounded and advisory; it never changes the coordinator model.
- Reviews and verifications use permitted routes without automatic escalation;
  record inspected model/effort when relevant. `review` is a label, not a route;
  use ordinary unless complexity or security evidence warrants another
  classification.
- Other models or efforts are outside this coordinator-selected policy.
  Preserve an explicit user-requested model/effort verbatim; do not reinterpret it
  as a Cortex recommendation. If a host cannot honor it, report an evidence gap.

Record the decision, concrete risk/uncertainty and selected model/effort before
assigning Sol/Astra. Governance changes verification depth, not this ladder.
These are selection instructions and diagnostic checks, never runtime gates.

Do not switch an active worker's model merely because it is slow or a host wait
expires.

## Assignments

The coordinator reads requirements, project instructions and bounded source evidence
needed to assess a report or decide acceptance. Delegate unknown-code discovery
to the appropriate specialist; do not investigate the same unknown code first and
then ask a worker to repeat it. An implementer may resolve bounded uncertainties
within its assignment; a separate explorer is useful only for a concrete unknown.
For structural discovery, specialists use available Codebase Memory MCP first,
following their code-and-evidence reference. Known instructions, configuration,
documentation and exact source checks need no redundant graph lookup. If the graph
is unavailable, stale or lacks relevant coverage, retain the concrete limitation
and continue with bounded native discovery; this is guidance, not a runtime gate.

Assign the complete requested deliverable, not only its research: when the user
requests a saved note or other file, include creating that file and verifying its
content/hash in the same worker assignment. The coordinator reconciles it after
the report; it does not finish omitted project-file edits itself.

Before delegation, use the project instruction's named documentation entrypoint
and follow only relevant links. A repository-wide file inventory is discovery,
not prerequisite instruction loading; leave it to the assigned specialist.

The coordinator retains the causal model across roles. An investigator answers a
specific uncertainty; an implementer owns changes and self-tests; an independent
reviewer/verifier examines the result against requirements and risks. Do not call
the implementer's own checks independent acceptance. Use separate reviewers for
nontrivial changes; combine work only when low risk and no independent check is
required. Research before implementation is useful for unknown causes, not a
mandatory stage for an already-understood edit. Select model/effort independently
for each assignment; governance changes depth, not the model policy.

Start new workers without inherited context and continue existing owners in retained
context. Write the assignment and every steering/follow-up message in English.
Start the assignment with the worker role and English-only reasoning, visible
summaries, commentary, questions, reports and final handoff requirement, effective
before the first skill/tool call. Do not paste the coordinator's language rule.
Quoted source and explicitly localized deliverables retain their required language;
they do not change the worker's own communication language. After context recovery,
repeat the role/language boundary. Correct observed drift in the next native message
without restarting, blocking, translating stored evidence or hiding original text.
Each concise assignment states the exact `$cortex:worker-...` skill,
complete loading before discovery/project work,
model/effort, policy class (`research`, `exploration`, `analysis`, `ordinary`,
`complex`, `security-analysis-microtask`, `consultation`, `consultation-narrow`,
`consultation-hard`, `consultation-deeper`, `decision`, `decision-hard`,
`decision-exceptional` or `review`), evidence, bounded outcome,
requirements/acceptance checks, owned files/resources/dependencies, source/report/
attachment references and handoff. Reviews retain implementation model/effort and
do not derive a route; include `Policy class: <value>`, review model/effort and
`User-requested override: yes|no` labels. Include the complete command result for
every code-mode call, including the initial skill read.

Worker updates, questions and blockers stay on the native parent/subagent channel;
do not route them through `codex_app.send_message_to_thread`, supply an app thread
ID, or ask workers to discover an app messaging tool. Native final responses deliver
the handoff automatically.

Native subagents spawned through `collaboration.spawn_agent` are tracked only with
the native collaboration controls: `collaboration.wait_agent`,
`collaboration.list_agents`, `collaboration.send_message` and
`collaboration.followup_task`. Never use Codex app thread tools such as
`create_thread`, `read_thread`, `wait_threads` or `send_message_to_thread` for
orchestration workers. Codex app thread tools are reserved for explicit user-owned
task management, not worker coordination.

After each native spawn, the coordinator keeps the owner unresolved until it has
performed bounded native waits and consumed that owner's terminal handoff/report.
It repeats a bounded wait after a timeout or pending result; neither is terminal
reconciliation, and neither authorizes a final answer. This is model-owned control
guidance, not a promise that hooks can resume a stopped host turn.

If worker Cortex MCP is unavailable, use only shared closed
`cortex-native-worker-result-v1`. Require one native spawn, terminal wait, parent/task,
registered profile, and assignment digest. The coordinator publishes
`Delegation evidence: native_worker_result` with `evidence_source=native_worker_result`,
`worker_mcp_unavailable=true`, `host_enforcement_state=unverified`, correlation IDs,
asserted hashes, and limits: coordinator evidence, never a worker report or host proof.
Missing, duplicate, truncated, replayed, or competing evidence blocks; never invent a
receipt or use generic shell work.

Include only the short loading requirement, not pasted protocols, schemas or
checklists. Workers load the selected complete skill normally and progressively load
applicable artifact skills.

Use these 23 profiles:

| Profile | Select for |
| --- | --- |
| `accessibility_auditor` | WCAG and assistive-tech audit |
| `accessibility_fixer` | Accepted accessibility remediation |
| `architect` | Consequential contracts and boundaries |
| `backend_dev` | APIs, logic, and persistence |
| `build_verification` | Independent build/release evidence |
| `code_reviewer` | Defect-focused change review |
| `data_engineer` | ETL, migration, and integrity work |
| `database_architect` | Schema, index, and query-plan design |
| `debugger` | Reproduction, root cause, and repair |
| `devops_engineer` | CI/CD and runtime configuration |
| `explorer` | Unknown paths, ownership, and impact |
| `frontend_dev` | Browser UI and tests |
| `fullstack_dev` | Cohesive client/server change |
| `general` | No justified narrower specialty |
| `mobile_dev` | iOS, Android, React Native, Flutter |
| `performance_engineer` | Measurement and optimization risk |
| `planner` | Execution-changing work breakdown |
| `qa_engineer` | Coverage, regression, and quality evidence |
| `refactorer` | Behavior-preserving restructuring |
| `security_auditor` | Auth, secrets, crypto, dependency risk |
| `senior_consultant` | One decision from selected reports |
| `technical_writer` | Source-backed documentation |
| `ux_designer` | Flows, hierarchy, responsive interaction |
