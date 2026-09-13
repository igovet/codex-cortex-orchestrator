# Worker routing

## Model and effort

Retain the user's selected coordinator model and effort. Do not propagate that cost
to every worker.

Apply this mandatory worker-model policy to every subagent assignment. Put model
and effort in the native request and repeat them in the assignment/pipeline; do
not rely on an inherited host default.

- Ordinary work defaults to `gpt-5.6-luna` at `medium`, `high`, `xhigh` or `max`.
  Luna is the priority route for bounded planning, implementation and
  documentation when no narrower rule applies. Research, exploration and analysis
  assignments are always Luna, regardless of whether the surrounding task is
  security-related.
- Complex work may use `gpt-5.6-terra` at `medium`, `high` or `xhigh`.
  State the coupled or consequential evidence warranting Terra; it is not a
  general-purpose fallback.
- Only a narrow security-analysis microtask may use `gpt-5.6-sol` at `medium`,
  `high` or `xhigh`; this is the sole exception to the Luna-only analysis rule.
  Sol is never an implementation route merely because the surrounding task
  concerns security.
- Security-related implementation uses Luna or Terra according to the ordinary or
  complex rule, never Sol; an audit and a fix are separate classifications.
- Opt-in `senior_consultant` handles one bounded question: Sol `medium` standard,
  Sol `low` narrow, Sol `high` harder; Astra `low`/`medium`/`high` only for a
  justified deeper escalation. This is configurable, never automatic, and never
  changes the coordinator model.
- Reviews and verifications use permitted routes without automatic escalation;
  record inspected model/effort when relevant. `review` is a label, not a route;
  use ordinary unless complexity or security evidence warrants another
  classification.
- Other models or efforts are forbidden for coordinator-selected work.
  Preserve an explicit user-requested model/effort verbatim; do not reinterpret it
  as a Cortex recommendation. If a host cannot honor it, report an evidence gap.

Do not switch an active worker's model merely because it is slow or a host wait
expires.

## Assignments

The coordinator retains the causal model across roles. An investigator answers a
specific uncertainty; an implementer owns changes and self-tests; an independent
reviewer/verifier examines the result against requirements and risks. Do not call
the implementer's own checks independent acceptance. Use separate reviewers for
nontrivial changes; combine work only when low risk and no independent check is
required. Research before implementation is useful for unknown causes, not a
mandatory stage for an already-understood edit. Select model/effort independently
for each assignment; governance changes depth, not the model policy.

Start new workers without inherited context and continue existing owners in retained
context. Each concise assignment states the exact `$cortex:worker-...` skill,
complete loading before discovery/project work, English-only communication,
model/effort, policy class (`research`, `exploration`, `analysis`, `ordinary`,
`complex`, `security-analysis-microtask`, `consultation`, `consultation-narrow`,
`consultation-hard`, `consultation-deeper` or `review`), evidence, bounded outcome,
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
