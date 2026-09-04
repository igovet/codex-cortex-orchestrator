<table>
  <tr>
    <td width="190" align="center" valign="middle">
      <img src="plugins/cortex/assets/logo.png" alt="Cortex logo" width="156" />
    </td>
    <td valign="middle">
      <h1>Cortex</h1>
      <p><strong>Reliable multi-agent coordination for complex software engineering work in Codex.</strong></p>
      <p>
        Cortex turns a large task into durable, evidence-backed coordination.
        It preserves tasks, typed execution graphs, reports, governance assessments,
        and closure records in a local ledger while leaving every
        orchestration and safe next-step decision to the model.
      </p>
      <p>
        <img src="https://img.shields.io/badge/Cortex-1.15.6-7c3aed" alt="Cortex 1.15.6" />
        <img src="https://img.shields.io/badge/Python-3.11%2B-3776ab" alt="Python 3.11+" />
        <img src="https://img.shields.io/badge/Codex-Desktop%20%7C%20CLI-111827" alt="Codex Desktop and CLI" />
        <img src="https://img.shields.io/badge/Ledger-SQLite%20schema%20v2-0f766e" alt="SQLite ledger schema v2" />
      </p>
    </td>
  </tr>
</table>

## Install with Codex — recommended

> [!IMPORTANT]
> **⚡ Copy the prompt below into a new Codex task.** It tells Codex to read this
> repository's current installation guide, install Cortex from the GitHub
> Marketplace `main` branch, install and configure Codebase Memory MCP,
> configure Codex, and verify the result.

```text
Install and configure the Cortex plugin for this Codex environment.

First, read the complete installation instructions and requirements in this
repository's README. Treat it as authoritative:
https://github.com/igovet/codex-cortex-orchestrator/blob/main/README.md

Then complete the setup end to end:

1. Check every prerequisite required by that README (including Codex plugin and
   multi-agent support, Python 3.11+ with tomllib, Git, and Bash). Install every
   missing prerequisite with the supported package manager for this operating
   system. Do not install unnecessary packages or Python dependencies.
2. Install and configure the Codebase Memory MCP server as `codebase_memory`,
   following the README's **Codebase Memory MCP** section and its linked
   official upstream instructions. Install its required dependencies, register
   it with Codex, enable automatic indexing when supported, and verify that
   Codex can use it for this exact project root. Restart Codex if its
   instructions require a restart.
3. For a fresh installation, add the Cortex Marketplace from the main branch
   exactly with:
   codex plugin marketplace add https://github.com/igovet/codex-cortex-orchestrator --ref main --json
   If the `cortex` Marketplace is already registered, refresh it with the
   documented update flow instead of adding a duplicate. Then install the
   plugin (or use the README's documented remove/reinstall update flow) with:
   codex plugin add cortex@cortex --json
4. Preserve existing ~/.codex/config.toml settings and add or correct every
   Cortex-required setting documented in the README: multi_agent_v2 = true and
   agents.default_subagent_model = "gpt-5.6-luna".
   Keep user approval review enabled; do not enable Ask for me / Approve for me.
5. Confirm that the installed package enables only the documented Cortex
   activation guard and sanitized lifecycle observer, then run the README's
   relevant verification checks. Start a new Codex task if the README requires
   it.

Use only the instructions and commands documented in that README. If an
elevated system permission, an interactive desktop confirmation, or a material
choice is required, explain exactly what is needed and ask me before proceeding.
```

> Cortex is activated only when you explicitly select it. An ordinary complex
> request—or merely mentioning orchestration—does not create a Cortex session.

## Table of contents

- [Install with Codex — recommended](#install-with-codex--recommended)
- [Installation](#installation)
  - [System requirements](#1-system-requirements)
  - [Choose a project root](#choose-a-project-root)
  - [macOS-specific notes](#macos-specific-installation-notes)
  - [Required Codex configuration](#required-codex-configuration)
  - [Post-install hook status](#required-post-install-hook-trust)
  - [Codex Desktop](#2-install-on-codex-desktop)
  - [Codex CLI](#3-install-on-codex-cli)
  - [Orchestration commands](#4-orchestration-commands)
  - [Existing repositories and harvest](#existing-repositories-use-harvest-when-needed)
- [Preferred worker route: Codebase Memory MCP](#preferred-worker-route-codebase-memory-mcp)
- [How orchestration works](#how-orchestration-works)
- [Profiles and model routing](#profiles-and-model-routing)
- [Developing Cortex](#developing-cortex)
- [Support Cortex 💜](#support-cortex-)
- [Verification and diagnostics](#verification-and-diagnostics)

---

## Installation

### 1. System requirements

| Component | Requirement | Why it is needed |
| --- | --- | --- |
| Codex | Desktop or CLI with Plugins and multi-agent support | Loads the plugin, skills, MCP server, and advisory agents |
| Python | **3.11+**, with the standard-library `tomllib` module | Runs the local Cortex MCP server and validators |
| Git | A current version | Fetches and refreshes the GitHub Marketplace source |
| Bash | **3.2+** | Runs repository development and local-source synchronization scripts |
| Operating system | macOS or Linux; WSL is recommended on Windows | The MCP server launches through `python3` and repository tooling uses Bash |

No additional Python packages are required through `pip`: the Cortex runtime
uses the Python standard library. Confirm that the required tools are available:

```bash
python3 --version
python3 -c 'import tomllib; print("tomllib: ok")'
git --version
codex --version
```

### Choose a project root

> [!WARNING]
> **Use a specific repository or worktree as `project_root`; never use an OS
> root or a broad system/home directory.** Each resolved root maps to one
> project ledger. A broad root creates the wrong project boundary and risks
> mixing unrelated task context in one private namespace.

Choose the exact project, for example `/workspace/my-service`. Cortex resolves
the root before deriving its project hash and requires it to exist as a
directory.

The installed MCP configuration invokes `python3` directly. Confirm that
`python3` resolves to Python 3.11+ in the environment that launches Codex. The
repository-only `CORTEX_PYTHON` override applies to local source synchronization
commands documented under [Developing Cortex](#developing-cortex); it does not
rewrite the installed MCP command.

### macOS-specific installation notes

The Plugins workflow is the same on macOS, but the local runtime may require a
separate Python 3.11+ installation:

- macOS does not guarantee a suitable Python 3.11+ runtime.
- macOS ships `/bin/bash` 3.2, which the repository helper scripts support.
- Homebrew uses `/opt/homebrew` on Apple Silicon and `/usr/local` on Intel; use
  `brew --prefix` instead of hard-coding either location.
- Apps opened from Finder or the Dock do not necessarily inherit shell startup
  files such as `~/.zprofile`, `~/.zshrc`, or `~/.bashrc`.

Install the prerequisites with [Homebrew](https://brew.sh/):

```bash
# Install Apple's command-line tools first if they are not already present.
xcode-select --install

brew install python@3.11 git
```

Resolve and verify the installed runtimes:

```bash
"/bin/bash" --version
"$(brew --prefix python@3.11)/bin/python3.11" --version
"$(brew --prefix python@3.11)/bin/python3.11" -c 'import tomllib; print("tomllib: ok")'
```

For Codex CLI sessions, make sure Homebrew is initialized before running
`codex`. Homebrew prints the exact `shellenv` command for the machine:

```bash
eval "$(brew shellenv)"
export PATH="$(brew --prefix python@3.11)/bin:$PATH"
python3 --version
codex
```

For Codex Desktop, ensure its launch environment resolves `python3` to the same
Python 3.11+ installation. A Desktop app opened from Finder or the Dock may not
inherit the shell `PATH`; configure the application launch environment or start
Codex from a shell where `python3 --version` is correct. Fully quit and reopen
Codex afterward, then start a new task.

The packaged MCP server and every activation/lifecycle callback deliberately
share the same host-resolved `python3 -B` launch contract. Cortex does not
hard-code `/usr/bin/python3`: Codex CLI and Desktop must both resolve
`python3` to Python 3.11 or newer in their own launch environments, while
`-B` prevents runtime bytecode from changing the content-addressed package.
When behavior differs between the two hosts, compare the Desktop launch
environment with the interactive shell first, then run the host preflight
against the same installed package; do not compensate with a host-specific
hook command.

### Required Codex configuration

> [!IMPORTANT]
> Configure Codex before the first Cortex 1.15.6 orchestration, then start a **new task**.
> Cortex requires the Codex multi-agent runtime, and the global default model
> for internal subagents must be **Luna**.

Add the following settings to `~/.codex/config.toml`:

```toml
[features]
multi_agent_v2 = true

[agents]
default_subagent_model = "gpt-5.6-luna"
```

The settings have distinct purposes:

- `multi_agent_v2 = true` enables exact native model and reasoning-effort
  selection for each subagent.
- `default_subagent_model = "gpt-5.6-luna"` lets Cortex select logical Luna
  without copying Luna into a native `model` override. Terra and Sol remain
  explicit overrides. Every selected effort is passed unchanged.

For clearer coordinator explanations and full plain-text question/answer
context, the recommended top-level Codex setting is:

```toml
model_verbosity = "high"
```

This setting is recommended rather than required. Apply it before starting a
new task so the task inherits the configured response style.

You may also approve all tools exposed by the local Cortex MCP server:

```toml
[plugins."cortex@cortex".mcp_servers.cortex]
default_tools_approval_mode = "approve"
```

The MCP setting affects Cortex ledger tools only. It does not authorize shell
commands, patches, external messages, destructive actions, scope expansion, or
tools from other plugins. Keep those decisions routed through Codex or the
user's configured approval policy.

> [!WARNING]
>
> ### Keep external and destructive approvals with the user
>
> Cortex governance is advisory. It is not an approval system and cannot
> replace Codex/user authorization for destructive, external, privileged, or
> materially scope-expanding actions.

### Required post-install hook trust

> [!IMPORTANT]
> **Cortex 1.15.6 ships an activation guard and a sanitized lifecycle
> observer.** Review and trust only the callbacks declared by the installed
> Cortex package. They apply only to an explicitly selected Cortex route.

The activation guard enforces host-side ordering around task anchoring and
native worker dispatch. It never rewrites a native spawn call: the unchanged
host call carries the exact server-rendered bootstrap. Current Desktop MCP
initialize carries no connection-specific thread/session identity, so Cortex
does not guess an initial audience from shared pending state. Every new
connection begins with a neutral complete catalogue and an uncommitted role;
that pre-identity catalogue grants no authority. `SubagentStart`
creates the real child-bound digest-only one-shot receipt, and the worker MCP
process atomically claims the later exact-call authorization from the same
owner-only plugin data directory on its first assignment read. Successful
terminal consumption commits worker role without a mid-turn catalogue
notification, avoiding replay of the already-successful Desktop bootstrap. An
explicit later `tools/list` returns only worker read/publication tools. A
Desktop client that retains the initial catalogue can still publish, while committed
server role checks reject every coordinator-only call. The hook never
repeats or stores bootstrap plaintext. Receipt routing is isolated by coordinator session
through an atomic active index; completed and foreign history is never scanned
or selected. The lifecycle observer records bounded structural markers needed
to verify real coordinator and worker sessions. Neither hook grants ledger
authority, invents completion, or replaces successful server-side assignment-
evidence consumption and worker-owned terminal publication.

Ordinary Desktop may launch plugin MCP processes with `HOME` but without an
explicit `CODEX_HOME` or hook-only `PLUGIN_DATA`. In that environment the
content-addressed MCP package derives the same `.codex/plugins/data/cortex-cortex`
directory from its verified installed cache topology. `env_vars` forwarding is
therefore optional for this identity path rather than a prerequisite that the
GUI host must manufacture.

Effective coverage is outcome-oriented: one `o_` item represents one
independent user obligation. Acceptance and verification criteria, constraints,
steer additions, and exact user-source fragments stay linked to that outcome
instead of inflating the report matrix. A steer revises its named outcome and
does not create a parallel coverage item.

### 2. Install on Codex Desktop

Codex Desktop and Codex CLI use the Plugins Marketplace system. The general
user workflow is documented in the
[official OpenAI plugin documentation](https://developers.openai.com/codex/plugins).

#### Add the GitHub Marketplace and install Cortex

> [!IMPORTANT]
> **Cortex is not published in the public plugin directory.** Add this GitHub
> repository as a Marketplace source before looking for Cortex in Desktop.

1. Open the **Plugins** tab in Codex Desktop.
2. Select **Manage** in the upper-right corner of the Plugins page.
3. Open the **Marketplace** tab under **Manage extensions**.
4. Select **Add marketplace**.
5. Complete the **Add plugin marketplace** dialog:

   | Field | Value |
   | --- | --- |
   | **Source** | `https://github.com/igovet/codex-cortex-orchestrator` |
   | **Git ref** | `main` |
   | **Sparse paths** | Leave empty; the Marketplace manifest is at the repository root |

6. Select **Add marketplace** and wait for confirmation. The source should
   appear in **Manage → Marketplace** as **cortex**.
7. Return to the Plugins directory, open **Personal**, and find **Cortex**. Do
   not search for it in the public directory.
8. Open the Cortex details page and select **+ / Install**.
9. Review the requested permissions, bundled MCP server, activation guard, and
   sanitized lifecycle observer.
10. Verify the [required configuration](#required-codex-configuration).
11. Start a **new Codex task**. Existing tasks do not load newly installed
    skills, MCP tools, profiles, or a different multi-agent adapter.
12. Open **Skills**, select **Cortex Orchestrator**, and describe your goal.

#### Update on Desktop

1. Open **Plugins → Manage → Marketplace**.
2. Find **cortex** and select **Upgrade marketplace**. To refresh every
   configured Git Marketplace, use **Upgrade all marketplaces**.
3. Return to **Plugins → Installed → Cortex**.
4. Install the available newer Cortex version. If the UI offers only uninstall
   and install actions, uninstall Cortex and install it again from **Personal**.
5. Confirm that the enabled hooks match the documented activation guard and
   sanitized lifecycle observer.
6. Recheck `multi_agent_v2` and the Luna default.
7. Start a **new Codex task**. An existing task may retain the previous plugin
   cache and catalog.

### 3. Install on Codex CLI

Register the GitHub Marketplace first. Cortex is not available in the public
plugin directory:

```bash
codex plugin marketplace add https://github.com/igovet/codex-cortex-orchestrator --ref main --json
```

Then start the interactive Codex CLI:

```bash
codex
```

Open the plugin browser:

```text
/plugins
```

Then:

1. Switch to the newly added **cortex** Marketplace tab.
2. Open **Cortex** and install it.
3. If needed, press `Space` to enable the installed plugin.
4. Confirm that the enabled hooks match the documented activation guard and
   sanitized lifecycle observer.
5. Verify the [required configuration](#required-codex-configuration).
6. Exit the current CLI session and start `codex` again.
7. In the new session, invoke `$cortex:orchestrator` or open `/skills`.

For a direct, non-interactive installation after adding the Marketplace, run:

```bash
codex plugin add cortex@cortex --json
```

#### Update on CLI

Open `/plugins`, select the **cortex** Marketplace, and install the newer
version. To refresh and reinstall it entirely from the terminal, run:

```bash
codex plugin marketplace upgrade cortex --json
codex plugin remove cortex@cortex --json
codex plugin add cortex@cortex --json
```

After every update, verify the required configuration, exit the current
session, and start a new one.

### 4. Orchestration commands

Cortex exposes one explicit entry point with several routes. On Desktop,
select **Skills → Cortex Orchestrator** or mention the skill in chat. In the
CLI, use `$cortex:orchestrator` or `/skills`.

| Command | Purpose | Example |
| --- | --- | --- |
| `$cortex:orchestrator <task>` | Start ordinary Cortex 1.15.6 coordination | `$cortex:orchestrator Find the race condition and fix it with tests` |
| `$cortex:orchestrator help` | Show read-only help without changing the project or ledger | `$cortex:orchestrator help` |
| `$cortex:orchestrator harvest` | Update missing or stale source-backed project knowledge | `$cortex:orchestrator harvest` |
| `$cortex:orchestrator harvest-refresh` | Re-audit and rebuild project knowledge documentation | `$cortex:orchestrator harvest-refresh` |
| `$cortex:orchestrator normal` | Leave the active Cortex route | `$cortex:orchestrator normal` |

Example tasks:

```text
$cortex:orchestrator Design and implement secure API-key rotation,
including migration evidence, tests, and residual-risk documentation.

$cortex:orchestrator Review the current change, identify regressions,
and synthesize independently verified findings.

$cortex:orchestrator harvest-refresh
```

#### Existing repositories: use harvest when needed

> [!IMPORTANT]
>
> ### Knowledge maintenance is an explicit route, not a lifecycle prerequisite
>
> Run `$cortex:orchestrator harvest` when an existing repository needs a
> source-backed knowledge baseline. Cortex 1.15.6 never blocks ordinary coordination
> because harvest has not run or project documentation is incomplete.

Start the knowledge update with:

```text
$cortex:orchestrator harvest
```

The resulting baseline uses the established project layout:

```text
docs/project/index.md
docs/project/conventions.md
docs/project/verification.md
docs/project/decisions.md
docs/project/gotchas.md
docs/features/index.md
docs/features/<feature>/index.md
```

`harvest-refresh` performs a full source-backed re-audit. Both routes remain
model-owned orchestration: the coordinator delegates every source/code/config
read, analysis, documentation edit, and verification to workers, while its
bounded index route only selects the knowledge contract for those delegations.
Their reports inform later delegations. The declared graph determines readiness:
dependent work waits for acceptable prerequisite evidence, while independent
ready work may run in parallel. The same artifact, revision and review bindings
apply to harvest as to other tasks; it is not an alternate execution protocol.

#### Documentation impact is assessed after verified tasks

After worker-reported project verification, the coordinator makes a
report-grounded documentation-impact decision. Material behavior, architecture,
interface, command, verification, convention, or feature-ownership changes
require a documentation-sync worker and a separate documentation-verifier
worker. No impact requires a finalized worker-owned report with an explicit
English documentation-impact section and material/no-impact rationale; when
existing finalized reports do not contain that section, a bounded
documentation-impact worker submits it. Closure derives coverage from the
current graph and its finalized evidence; a coordinator-authored no-impact
assertion cannot substitute for a worker report.
The coordinator never submits a report on a worker's behalf. Documentation is
durable navigation, not runtime authority. Source, executable configuration,
schemas, and tests remain authoritative when prose drifts.

> [!CAUTION]
> Never place secrets, personal data, raw worker reports, private diagnostic
> logs, or credentials in generated documentation.

---

## Preferred worker route: Codebase Memory MCP

> [!WARNING]
>
> ### Use Codebase Memory MCP for preferred project structural discovery
>
> **[DeusData/codebase-memory-mcp](https://github.com/DeusData/codebase-memory-mcp)**
> builds a local graph of functions, classes, calls, routes, and dependencies.
> Cortex workers can use it for architecture discovery, impact analysis, and
> end-to-end tracing, especially in large monorepos.
>
> Codebase Memory is a preferred worker route: every native worker uses it when
> available before structural project-code discovery. If the MCP is missing,
> denied, timed out, erroneous, unusable, or insufficient, the worker records
> that bounded limitation and uses exactly one safe assignment-scoped
> ordinary-repository fallback. There is no silent or chained fallback. The
> coordinator is deliberately denied operational access; the
> shared host catalog may still make the server name visible to it.

Quick install on macOS/Linux:

```bash
curl -fsSL https://raw.githubusercontent.com/DeusData/codebase-memory-mcp/main/install.sh | bash
```

Review the remote installation script before running it. For Windows, manual
installation, and package-manager options, see the
[official Codebase Memory README](https://github.com/DeusData/codebase-memory-mcp#quick-start).

After installation:

1. Restart Codex so it loads the MCP server.
2. Ask Codex to index the exact project root, or enable automatic indexing:

   ```bash
   codebase-memory-mcp config set auto_index true
   ```

3. Confirm that the indexed root matches the exact absolute `project_root`.

Codebase Memory is an evidence source and preferred worker capability, not a
Cortex ledger capability. Its absence allows one bounded safe fallback; only
inability to establish the assigned surface after that fallback is a worker
blocker. The ledger can preserve limitation evidence and support an honest
closure or final answer.

---

## How orchestration works

Cortex combines a model-owned plan with a typed, transactional integrity core.
The coordinator decides what work is useful and which ready specialists to
dispatch. The core stores the graph and verifies authority, dependencies,
ownership, artifact generations and user decisions. It does not choose a
profile, schedule a worker, interpret a product branch, or manufacture evidence.

The implementation and qualification contract is
[Typed orchestration integrity](docs/project/typed-orchestration-integrity.md).
Its Completion checklist distinguishes source regression evidence from actual
CLI/Desktop qualification. An unfinished live gate is not a release pass.

The obligation-preserving redesign is still in progress. Registered root-input
capture and incremental extraction storage are source-tested components, not a
completed replacement public interface. Passive capture preserves exact selected
input privately but does not authenticate its human origin. Drafts and Markdown
do not establish completion; the checklist retains the remaining integration
and real-host qualification gates.

### Activation and project access

Only explicit selection of `cortex:orchestrator` activates this workflow.
Ordinary complexity does not. The `normal` route leaves orchestration; help and
knowledge-harvest routes have their separately documented boundaries.

A fresh coordinator opens the complete semantic task before project execution,
then records its governance assessment before any assignment. It preserves each
outcome's acceptance conditions, constraints and expected checks without
merging independently actionable requirements. The coordinator delegates project
inspection, implementation and verification. Its direct knowledge-routing
exception is limited to the known project/feature indexes and relevant linked
pages; it is not general source-search authority.

Worker discovery prefers Codebase Memory when usable for the exact project
root. Unavailable or insufficient graph evidence allows one bounded safe
fallback, not repeated broad discovery. The worker records the limitation
without treating absence of an optional knowledge service as a product blocker.

### Typed execution graph and readiness

Planning is proportional to the work. A genuinely bounded minimal task can use
a backend-generated complete-contract graph. Nontrivial work uses a planner-owned
candidate DAG, followed by independent semantic graph validation. Structural
validation alone cannot establish that the proposed dependencies make sense.

The graph declares semantic node keys, dependencies, required and produced
capabilities, unique contribution owners, verification subjects, execution modes
and bounded completion/remediation policies. Several contributions may jointly
complete one outcome, and several independent audits may verify it. A producer
cannot self-certify independent verification of its contribution.

A candidate cannot execute until it passes structural and independent semantic
validation. Where review is required, activation still leaves execution waiting
for the exact current approval. A rejected candidate remains historical and
permits bounded, progressive replanning, not repeated publication of the same
plan with different prose.

The usual implementation order is:

1. Initial artifact baseline, then bounded research and planning with independent candidate validation.
2. Implementation and baseline checks that produce the artifacts to inspect.
3. Dependent audits, parallel when they are ready and compatible.
4. In-contract fixes and independent regression for confirmed defects.
5. Documentation edits, when needed, before final verification.
6. Final verification and read-only documentation-impact assessment on the
   latest sealed generation.
7. Result presentation and mandatory revise-or-close review.

An architecture or database implementation audit cannot be assigned before its
implementation prerequisites are acceptable. It must not start merely to
return `partial` for an artifact that was not ready.

Scope reads expose current ready/waiting nodes and bounded reasons. Assignment
admission rechecks the same prerequisites atomically, claims one owner and
renders immutable scope from the graph. The coordinator selects current node
keys; it does not recreate scope, acceptance or predecessor evidence in a
second free-form assignment.

Native capacity is separate from semantic readiness. A ready node can wait for
a host slot without changing the graph or asking the user to continue.
Independent read-only work may run in parallel. The shared-checkout barrier
admits only one artifact mutator at a time and excludes overlapping
artifact-dependent readers; it is not a general one-worker restriction.

### Native workers and terminal results

Each successful, non-replayed assignment returns one exact native dispatch.
The coordinator forwards it immediately and unchanged to the supported
zero-history native spawn operation. Profile policy stays in the immutable
assignment; it is not an invented native agent-type argument.

The worker receives one typed assignment with its node-local checks and artifact
procedure, plus complete scoped outcome context. Contextual product verification
requirements do not become extra work or substitute publication keys. Old
assigned/planning-item views and serialized duplicate node instructions are not
part of worker evidence.

Private hook observations correlate the exact dispatch with the actual native
child. A worker first consumes its server-issued assignment, then performs only
that scope and publishes once through the predeclared terminal kind:

- planning nodes publish plans;
- documentation nodes publish documentation assessments;
- other nodes publish results, even when read-only or owned by a writer profile.

Publication kind follows node purpose, not profile or whether files changed.
A coordinator or sibling cannot consume another worker's authority. A retained
initial MCP catalogue does not bypass server-side actor checks, and execution
does not depend on a mid-turn catalogue refresh.

Result/documentation coverage is the single caller-authored source of observed
verification. Mandatory checks must be executed successfully for completion.
Failed, incomplete and unrun evidence remains explicit. Plans contain expected
checks, never fabricated observed facts. Artifact commitments are integrity
metadata, not a duplicate verification narrative.

### Artifact consistency and recovery

Workers compute the declared Git-state or bounded path-manifest fingerprint.
The core stores observations; it does not inspect the project filesystem.
Read-only evidence must begin and end at its assigned sealed generation.
A mutator begins there and seals a successor generation with its changed-path
commitment. Earlier artifact-dependent evidence remains historical.

A snapshot conflict is a static non-publication result. It creates no report,
consumes no terminal slot and raises a reconciliation barrier. Reconciliation
waits for verified native quiescence, observes the actual project state and
seals a new baseline. It does not claim to undo a stopped worker's writes.
Workers must not leave detached mutating processes after final observation.

A direct user change is committed immediately, even while an earlier plan
question or worker is pending. The atomic revision invalidates old authority
and returns the affected protected native task names. The coordinator
interrupts matching stale agents through supported host calls. New-revision
planning and execution wait for native reconciliation and the observed
baseline. A racing old publication returns `superseded` without creating
evidence or granting permission to retry.

Recovery of an existing task begins with current state. If unfinished delegated
work exists, the terminal continuation read precedes further task progress.
Timeline is reserved for an actual chronology/audit need, not recovery.
Loss requires complete supported native evidence and absence of a finalized
publication; timeout, silence and an isolated stop hook are not sufficient.
Replacement follows existing lineage and finite recovery budgets.

### Decisions, fixes and closure

Minimal/light complete plans can proceed informationally. Complexity alone
does not require approval. Material high risk, explicit review requests and
genuine product/authority choices require a decision-ready plan packet.
Approving a plan grants no native filesystem, deployment or credential
permission beyond authority already available.

When alternatives can all be specified safely, one plan publication contains
complete candidate alternatives. Each is independently validated against its
proposed contract. One review answer selects and approves exactly one branch;
its semantic delta, new revision and selected graph commit atomically.
Unselected alternatives never become executable. When responsible alternatives
cannot yet be constructed, a genuine pre-plan steering question is appropriate.
No-op steering is not a valid semantic revision.

Approval fulfills its decision boundary; direct user steering does not
automatically create another approval question. New material risk or authority,
or an explicit renewed-review request, requires a fresh boundary. Plan-review
authority binds the candidate, its validation, artifact state and governance
assessment. A changed boundary invalidates an unanswered old packet without
blocking authorized replanning.

An in-contract defect activates only the plan's validated remediation policy.
The graph records the repair, independent regression, original finding and
finite strategy/evidence budgets. Successful regression resolves capabilities
in current projection without rewriting the failed report. Non-progress and
budget exhaustion remain honest incomplete evidence, never endless retries or
automatic scope expansion. Scope/authority/risk-changing findings cannot
silently activate an ordinary repair.

Before closure, the coordinator presents the verified result, documentation
impact, remaining risks and unrun checks, with server-verified report links.
It then asks whether to revise or close. Only the direct current close choice
permits a closure attempt; an earlier plan approval or request to finish
automatically does not. Backend graph, generation and review-freshness checks
still apply. Incomplete work cannot be closed, including after a user agrees
to close; a risk-bearing verdict cannot waive mandatory checks.

### Storage, receipts and verified views

The current project-sharded SQLite ledger creates its current schema directly;
unsupported layouts are rejected, not migrated or used as compatibility
fallbacks. Historical installations remain untouched. Current task identity is
server-issued, and worker references are audience-bound capabilities.

Each command commits its transition, event and server-derived receipt in one
transaction. An identical retry after an ambiguous response reconciles the
existing commit; changed input conflicts. A successful non-replayed dispatch
does not permit another native spawn. Publication consumes one cross-kind
terminal slot.

Human-readable Markdown is a derived view, not authority. A link is returned
only after rendering, readback and digest verification. View failure after
report commit preserves the report. One transient I/O failure receives a bounded
same-request repair from the durable report; persistent failure remains explicit.
Exact receipt reconciliation may repair the view without publishing again.
Unsafe paths, permission denial and external-edit conflicts are not bypassed.
Review cannot substitute an invented path
for a missing verified link.

Closure certifies the latest observed generation, not continuous filesystem
immutability. No current host API supplies an atomic filesystem seal across
the final worker observation and user closure. Qualification therefore controls
external writers across that interval and reports the boundary honestly.

The twenty live advertised schemas and descriptions are the sole MCP argument
authority. Skills, worker messages and ordinary live workloads describe meaning
and policy, not duplicated argument shapes.

---
## Profiles and model routing

Cortex includes 22 advisory specialist profiles:

| Area | Profiles |
| --- | --- |
| Discovery and planning | `explorer`, `planner`, `architect`, `database_architect` |
| Implementation | `frontend_dev`, `backend_dev`, `fullstack_dev`, `mobile_dev`, `data_engineer`, `devops_engineer`, `general` |
| Diagnosis and improvement | `debugger`, `refactorer`, `performance_engineer`, `ux_designer`, `accessibility_auditor`, `accessibility_fixer` |
| Quality control | `qa_engineer`, `code_reviewer`, `security_auditor`, `build_verification` |
| Documentation | `technical_writer` |

Profiles describe useful roles, operating workflows, quality bars, and
escalation conditions. They never select a model, authorize a public tool, or
enforce a capability.

### Adaptive model policy

The coordinator chooses one exact model and reasoning effort independently for
every delegation. Governance mode does not choose a single pair for the task.
`profiles.json` is the canonical recommendation source:

| Exact model | Recommended effort | Choose for |
| --- | --- | --- |
| `gpt-5.6-luna` | through `max` | Default for most bounded work, including Explorer/discovery, ordinary implementation, QA, and deterministic rechecks; increase Luna effort before changing models |
| `gpt-5.6-terra` | through `max` | Only genuinely complex planning or architecture, including cross-cutting design decisions or processes that cannot be safely resolved by Luna at the selected effort |
| `gpt-5.6-sol` | rare, through `max` | Only genuinely very-high-risk security-related work |

Cortex permits `low`, `medium`, `high`, `xhigh`, and `max`; `ultra`
is never valid. These values are a native transport support boundary, not a
backend policy matrix; the coordinator chooses the exact effort needed for
each assignment.

Every native subagent dispatch preserves isolated history and the exact
coordinator-selected effort; the matching durable delegation retains the exact
model choice. Codex forwards the returned closed projection once to the active
host spawn operation; one durable delegation maps to exactly one host spawn:

- Luna omits the model override so the configured default Luna is selected, and carries the exact effort.
- Terra carries the exact selected Terra model and effort.
- Sol carries the exact selected Sol model and effort.

The backend validates but never derives or rewrites a coordinator-selected
pair. There is no server-owned Luna → Terra → Sol escalation. A replacement
delegation always receives a fresh coordinator choice.
Explorer and ordinary discovery always use Luna. Terra is selected only when
genuinely complex planning or architecture is evidenced; Sol remains rare and
reserved for genuinely very-high-risk security-related work.

---

## Developing Cortex

> [!CAUTION]
> End users install and update Cortex through the GitHub Marketplace flow above.
> `./scripts/sync-cortex.sh` is the supported repository-development and local
> source synchronization path for this checkout; it is not a replacement for
> the public installation instructions.

### Runtime boundary

The complete installable product lives under `plugins/cortex/`: the manifest,
profiles, authoritative skills, MCP configuration, and runtime. Root-level
`scripts/`, `tests/`, `docs/`, and `AGENTS.md` support repository development
and are not installed runtime authority.

Important entry points:

| Path | Purpose |
| --- | --- |
| `plugins/cortex/scripts/cortex.py` | Cortex 1.15.6 MCP server facade |
| `plugins/cortex/.mcp.json` | Direct Python MCP server startup configuration |
| `plugins/cortex/scripts/cortex_runtime/v12_contract.py` | Bounded task/report constants and canonical report digests |
| `plugins/cortex/scripts/cortex_runtime/v12_store.py` | Project-isolated current-schema storage |
| `plugins/cortex/scripts/cortex_runtime/v12_projections.py` | Host-private derived Markdown materialization |
| `plugins/cortex/scripts/cortex_runtime/v12_maintenance.py` | Task-anchored host-private operator CLI outside MCP |
| `plugins/cortex/scripts/cortex_runtime/worker_message.py` | Attested direct native worker-message rendering |
| `plugins/cortex/scripts/cortex_runtime/public_contracts.py` | Exact uniform twenty-tool semantic catalog |
| `plugins/cortex/profiles.json` | Advisory profiles and model recommendations |
| `plugins/cortex/skills/orchestrator/SKILL.md` | Single authoritative outcome-first orchestration skill |
| `plugins/cortex/skills/cortex-control/SKILL.md` | Authoritative twenty-tool semantic, nonblocking, and task-anchor semantics |
| `.agents/plugins/marketplace.json` | Repository-local Marketplace |
| `scripts/sync-cortex.sh` | Synchronize and verify this local source checkout during development |
| `scripts/cortex-dev` | Start an interactive Codex session in the persistent isolated `$HOME/.cortex-dev` candidate runtime |
| `scripts/cortex-desktop-dev` | Prepare that candidate and launch the real Codex Desktop with a disposable Electron profile |
| `cortex-dev` | Repository-root convenience entry point delegating to `scripts/cortex-dev` |
| `scripts/cortex-dev-reset` | Remove that candidate runtime only after explicit `--confirm` |

The installable release includes the activation guard and sanitized lifecycle
observer declared in `plugins/cortex/hooks/hooks.json`. They are host-side
ordering/observation callbacks only; all task, assignment, evidence,
publication, decision, governance, and closure authority remains in the
uniform MCP server.

### Isolated candidate runtime

Use the repository helper for interactive development. It resolves this
checkout from the helper's own location, creates or reuses the dedicated
`$HOME/.cortex-dev` directory with owner-only permissions, exports
`HOME=$HOME/.cortex-dev` and `CODEX_HOME=$HOME/.cortex-dev/.codex` inside that candidate
runtime, synchronizes the checkout there, and then starts ordinary interactive
Codex. The candidate HOME, `CODEX_HOME`, plugin cache, configuration, and Cortex 1.15.6
state are isolated from the stable runtime. One explicit exception supplies the
required worker MCP: `cortex-dev` projects the enabled production
`mcp_servers.codebase_memory` command/approval settings into the candidate and
runs only that external MCP child with its owning production HOME. It never
modifies the production config or copies arbitrary environment/credential
tables. The normal checkout synchronization
can still update source metadata or generated content and remove disposable
plugin bytecode; the helper isolates those effects to the candidate workflow but
does not make the source checkout immutable.

```bash
./scripts/cortex-dev
```

The equivalent repository-root convenience command is `./cortex-dev`; it
delegates to the same launcher and accepts the same ordinary Codex arguments.

For a real Desktop check, use the dedicated launcher. It prepares the same
candidate, then opens the actual Desktop binary with isolated `HOME`,
`CODEX_HOME`, and Electron user data. The stable Codex profile and installed
Cortex plugin remain untouched:

```bash
./scripts/cortex-desktop-dev start --workdir /absolute/test/project \
  --prompt-file TASK_PROMPT.txt --model gpt-5.6-luna --reasoning-effort high
./scripts/cortex-desktop-dev status
./scripts/cortex-desktop-dev stop
```

CLI/Desktop parity requires consecutive successful real-host scenarios on the
same stamped payload, in either order. Any installable-payload edit invalidates
both results and restarts the pair.

The helper accepts ordinary Codex arguments after the script name. Its `--help`
mode only prints usage. The candidate reset helper requires an explicit
confirmation token and refuses the active HOME, repository, broad paths,
symlinked targets, and non-regular entries:

```bash
./scripts/cortex-dev-reset --confirm
```

Run reset only when the shell's active HOME is the original home that owns the
dedicated `.cortex-dev` directory. The helper is deliberately limited to that
exact candidate path; it cannot reset the stable runtime or arbitrary data.

### Recommended development loop

The preflight optionally exports a private passive capability snapshot with
`--host cli` (or `desktop`) and `--capability-output /absolute/new-file.json`.
Use an owner-controlled qualification directory; an existing file is never
overwritten. Package/configuration checks do not prove native coordination,
hook delivery, input provenance or recovery. Those capabilities remain
`unverified` until actual host observations are available. The new injected
host-adapter boundary is source-tested, not yet CLI/Desktop live-qualified;
see the [integrity completion checklist](docs/project/typed-orchestration-integrity.md#11-completion-checklist).
Both isolated launchers save a separate passive snapshot before starting their
host, under an owner-private `.host-qualification-*` directory in the isolated
Codex home. Preparation without launching a host does not produce a live result.

```bash
# 1. Inspect the checkout and host without changing Codex configuration
PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/cortex-host-preflight.py

# 2. Run source contract checks
PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/cortex-prompt-lint.py
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=plugins/cortex/scripts python3 -B -m pytest -q

# 3. Preview the source update without writing
./scripts/sync-cortex.sh --dry-run

# 4. Start the isolated candidate runtime; candidate state is isolated, while
#    normal synchronization may update source metadata/generated content and
#    remove disposable plugin bytecode
./scripts/cortex-dev

# 5. Open a new ordinary interactive Codex task and test the changed behavior

# 6. Confirm the candidate environment's synchronized copy matches the checkout
CORTEX_DEV_HOME="${HOME}/.cortex-dev" HOME="$CORTEX_DEV_HOME" CODEX_HOME="$CORTEX_DEV_HOME/.codex" ./scripts/sync-cortex.sh --check
```

`sync-cortex.sh` validates package metadata, synchronizes `cortex@cortex`,
enables `multi_agent_v2`, and enforces the required Luna default while
preserving unrelated configuration. In normal synchronization mode it also
removes only disposable Python bytecode beneath `plugins/cortex` and refreshes
the marked orchestrator routing table from `profiles.json`; read-only modes do
not rewrite source state. It does not import, migrate, or modify user V11
ledgers or unrelated plugin data. Run normal synchronization through
`scripts/cortex-dev` during active development so those changes land only in
the candidate runtime. Normal synchronization may update source metadata,
generated content, and disposable plugin bytecode in the checkout, so direct
normal synchronization is an explicitly authorized source-checkout operation,
not the public Marketplace install flow.

To select another Python interpreter:

```bash
CORTEX_PYTHON=/absolute/path/to/python3.11 ./scripts/sync-cortex.sh --dry-run
CORTEX_PYTHON=/absolute/path/to/python3.11 ./scripts/sync-cortex.sh
```

### Operator maintenance

Cortex 1.15.6 also packages a local administrator CLI for explicit health, project-shard
backup, checkpoint, optimize, vacuum, offline restore, derived-projection
prune/regeneration, and sealed-backup retention. It is **not** an MCP tool and
does not change the complete twenty-tool semantic registry or either
audience projection. Every operation starts from an
existing Cortex 1.15.6 `task_id`, derives the host-private shard from that ID, accepts no
`project_root` or arbitrary destination, emits bounded sanitized JSON, touches
no V11 state, and writes nothing to the target project.

From this checkout, run the module from the packaged scripts directory:

```bash
cd plugins/cortex/scripts
export CORTEX_TASK_ID='paste-exact-task-id-here'
PYTHONDONTWRITEBYTECODE=1 python3 -B -m cortex_runtime.v12_maintenance health \
  --task-id "$CORTEX_TASK_ID"
```

Mutating commands require their exact uppercase confirmation token. Backups
cover the entire selected project shard, not only the anchor task. Restore is
strictly offline: stop every normal Cortex MCP process using the shard first,
then supply `RESTORE`, the exact task and `p-<hash>` shard, and
`MCP_STOPPED`. That final value is an operator assertion; the CLI cannot stop or
lock out a live MCP server. Projection pruning and backup retention default to
dry-run and never remove canonical ledger rows.

The complete commands, safety boundaries, and verification contract are in
[operator maintenance](docs/features/operator-maintenance/index.md).

### Versioning

The current Cortex public contract release is **1.15.6**. Version and build identity are
defined by `plugins/cortex/.codex-plugin/plugin.json`. The installable manifest always
uses `1.15.6+codex.sha256.<digest-prefix>` in both the GitHub Marketplace package and
the isolated development candidate; the MCP server continues to advertise semantic
version `1.15.6`.

When changing the plugin, update the version according to SemVer:

- patch for a fix without new functionality;
- minor for a backward-compatible feature;
- major for a large or breaking change.

Build metadata after `+` is content-addressed as
`codex.sha256.<digest-prefix>`; it identifies the exact isolated candidate and
the exact production package and cannot be reused for different bytes. Runtime
startup recomputes the packaged digest before MCP initialization and rejects a
missing, stale, or invented suffix outside explicit source mode. An explicitly
source-mode checkout may use plain `1.15.6` or retain its last stamped suffix
while edited and reports `parityVerified=false`; neither is an installable
release until release validation stamps the exact current digest. The
product/server compatibility boundary remains `1.15.6`. V11 tools and unfinished
V11 tasks are not compatible with Cortex 1.15.6.

### Development agreements

- Keep one authoritative bundled orchestrator skill and the complete static
  twenty-operation registry. Retained catalogues never replace per-call actor
  checks.
- Keep project execution worker-owned and knowledge routing bounded to the
  installed skill's exact known-path policy.
- Derive immutable scope and predecessor evidence from current typed nodes;
  never preserve prose-assignment or old-publication compatibility routes.
- Use the exact packaged profile as assignment policy, never an unsupported
  native agent type. Forward one unchanged server-rendered dispatch.
- Preserve original user evidence in its designated fields; keep worker
  engineering narrative English and localize coordinator presentation.
- Store one canonical observed-coverage narrative. Expected plan checks and
  artifact integrity metadata are not duplicate observations.
- Enforce transactional ownership, dependency, artifact, revision, recovery,
  review and closure gates without turning the backend into a scheduler.
- Keep remediation bounded, independently verified and inside the validated
  contract. Ordinary in-contract fixes do not ask the user to continue.
- Bind documentation impact and final verification to the latest sealed
  generation; documentation mutations precede final verification.
- Treat human views as private verified projections, never authority or
  reconstructed paths.
- Keep operator maintenance outside MCP and restore offline, with exact
  confirmations and independently established quiescence.
- Use apply_patch for source edits. Refresh only the isolated candidate through
  the supported development launcher; never synchronize the stable installation
  as a development test.
- Preserve the semantic version during this hardening work and refresh the
  content-addressed cache suffix after every payload change.
- Keep the implementation document's Completion checklist current. Source
  tests do not satisfy native live gates; final CLI/Desktop qualification must
  use the same unchanged payload and mandatory final closure review.
- Never commit runtime ledgers, raw reports, private diagnostic logs, secrets
  or personal data.

---

## Support Cortex 💜

Cortex remains open source. If it is useful to your work, you can support its
continued development and maintenance through
[GitHub Sponsors](https://github.com/sponsors/igovet). Sponsorship helps fund
testing and experimentation with multi-agent coordination, plus the tooling,
documentation, skills, and MCP integrations that make it better. Sponsorship is
entirely optional.

---

## Verification and diagnostics

Run the release/protocol gate before publishing a change:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=plugins/cortex/scripts python3 -B -m pytest -q
```

The gate builds the allowlisted source candidate, compiles the bundled Python,
starts an isolated MCP server, asserts the complete twenty-tool semantic
registry and both audience projections,
and exercises current-only storage, atomic receipts, typed DAGs, concurrent
claims, bounded publication evidence, candidate-family selection, review
freshness, artifact-bound verification, finite remediation and recovery,
host-private projections, cross-project isolation, activation/lifecycle hooks
and exact model/effort transport. These are source checks, not actual native
CLI/Desktop qualification.

Run the bounded supporting diagnostics:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/cortex-prompt-lint.py
PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/validate-cortex-marketplace.py
PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/verify-cortex-release.py --mode source
git diff --check
./scripts/sync-cortex.sh --dry-run
```

The self-contained skill/profile lint checks the bundled runtime contract and
advisory profile invariants without making a model call. It is structural source
evidence, not behavioral evaluation. Actual coordinator behavior is verified in
fresh ordinary interactive Luna/high `codex` tasks inside tmux.

## Interactive tmux live-dev workflow

Before submitting any workload, the LLM/operator must observe the passive
host-owned activation receipt for the exact isolated candidate. The receipt
proves agreement between candidate identity, registered Cortex server, and
advertised catalogue identity; it is observation-only, and the transport never
parses or judges it. A missing receipt makes the environment unverified and the
workload must not be sent. Once `cortex:orchestrator` is selected, the first
project execution action must be the catalogued `open_task` operation. Prose
activation acknowledgement, shell/repository inspection, project-state checks,
or worker dispatch before task opening is a route violation.

Use the real operator-controlled ordinary Codex session. `./scripts/cortex-dev` refreshes the isolated candidate but does not create tmux; `./scripts/cortex-live-smoke start` creates the exact session on the default server with an ordinary `bash` pane, attaches an owner-only output-only `pipe-pane` stream to that exact pane, and only then inserts the fixed launcher command literally and submits it with one standalone Enter. The launcher prints `Cortex live-dev exit=<status>` and exits with that same status.

```bash
./scripts/cortex-live-smoke start
./scripts/cortex-live-smoke status
./scripts/cortex-live-smoke capture
./scripts/cortex-live-smoke events
TERM=xterm-256color tmux -f /dev/null attach -t cortex-v12-smoke
# Only if the visibly observed fresh-project trust screen asks for acknowledgement:
./scripts/cortex-live-smoke enter
./scripts/cortex-live-smoke send --prompt-file TASK_PROMPT.txt
./scripts/cortex-live-smoke capture
./scripts/cortex-live-smoke stop
```

After `start`, `capture` provides the bounded output-only PTY stream when detached `capture-pane` is stale. `events` provides the exact session's bounded owner-only sanitized MCP observation stream; it carries only safe operation/outcome metadata and is never an automated readiness or acceptance parser. Visibly confirm the Codex state in `attach` or `capture` before any input; `pane_current_command=codex` alone is insufficient because early text or submission can be lost during TUI initialization. If a visibly observed fresh-project trust screen asks for acknowledgement, the operator/LLM may use `enter` exactly once; that transport action sends one standalone Enter to the exact pane, does not auto-trust a directory, and does not edit Codex trust configuration. Then visibly confirm the interactive composer before `send`. Every workload begins with the real `$cortex:orchestrator` token. That token is the prompt's only Cortex-specific content; the remainder is an ordinary user request for a concrete product change, development task, diagnosis, or verification. Environment constraints, internal stages, tool policy, replay handling, worker/coordinator instructions, and pass/fail sentinels belong to the external operator and verifier, never to the workload prompt. The controller normalizes the prompt to one line, inserts it literally with one `send-keys -l` delivery, waits five real seconds after insertion returns, and sends exactly one standalone named `Enter` to the same pane. Its receipt reports delivery only; it never claims TUI acceptance. The coordinator/LLM confirms acceptance and progress from the pane and bounded events. Observe actual task-relevant Cortex MCP calls and results: `Cortex tool error`, `validation_error`, `schema_unsupported`, traceback, or missing expected completion is failure. A repeated successful mutation without an explicitly ambiguous prior transport result is also failure. Capture the exit marker before stopping; cleanup stops the pipe and removes only `cortex-v12-smoke` plus its owner-only temporary capture. Never use `codex exec`, an alternate socket, or stable HOME/CODEX_HOME.

Prompt delivery contract: after the composer is visibly confirmed, normalize the prompt to one line, send the complete prompt literally with one `send-keys -l` delivery, wait a real five seconds after that insertion returns, then send exactly one standalone named `Enter` to the same pane. Do not send a pre-submit `C-m` or `C-j`; the transport receipt reports delivery only and the coordinator/LLM confirms TUI acceptance.

For every native worker spawned by live orchestration, the LLM verifier must inspect a bounded sanitized structured event stream as well as the coordinator pane because worker MCP calls/errors may be hidden. The helper may expose events but must not decide pass/fail. Acceptance requires a clean first worker-owned publication success, zero prior hidden validation/tool errors or mutation replays; a final publication reference alone is insufficient.

The E2E acceptance case is multi-turn and runs in a separate test project. The LLM observes the pane, approves the visibly rendered high-risk/material plan (including any predefined branch or API-key/ENV answer), and follows planner → implementation → independent verification → documentation-impact assessment → closure. While a worker is active, the operator also inserts a bounded burst of ordinary in-scope messages and verifies that they are processed in order without dropped or duplicated decisions. It inspects every native worker event stream and fails on any hidden tool error or unexplained replay. The tmux transport never answers or approves autonomously.

The complete live catalogue scenario is defined by
`tests/fixtures/live_cortex_all_tools_scenario.json` and its ordinary workload
prompt. Run it with Luna High against the same unchanged stamped candidate in
CLI and real Desktop consecutively. The external LLM verifier, not the
transport, confirms at least one clean task-relevant success for every one of
the twenty public operations, performs the specified same-thread CLI resume,
and rejects any hidden tool error or unexplained mutation replay. Conditional
operations are reached through real prerequisites: point replacement, active
host recovery, an explicitly requested chronology, actual behavior steering,
and the final closure review. `tests/test_full_orchestration_matrix.py` is only
a source/API state-transition matrix; it neither substitutes for nor claims
MCP, hook, native-worker, resume, or two-host evidence.

Keep live checks narrowly targeted to the modified function, tool, or contract;
do not substitute an exec-mode wrapper or detached session. The launcher prints
the isolated `HOME` and `CODEX_HOME` target before it synchronizes, so record
that target, the refreshed cache version, the exact session command, scope,
outcome, and any unrun checks. If ordinary Codex cannot start or a terminal
permission prompt/denial prevents the targeted input or result, report the
smoke as failed or unverified from the bounded capture; never infer success.
Always clean up the named session. Never install, reinstall, update, or
synchronize the user's real installed plugin for a repository live-dev smoke.

After installation or update, run exactly one fresh interactive Cortex session
first. Require worker-verified acceptance and an advisory `ready` closure with
no coordinator boundary violation. Only after that single-session pass may any
concurrent multi-session smoke begin.

Qualification is layered, as specified in
[Typed orchestration integrity](docs/project/typed-orchestration-integrity.md#10-qualification-ladder):
local operation/negative-contract tests, profile publication contracts, DAG and
artifact races, steering/recovery, a short real CLI E2E, full CLI, then full real
Desktop on one unchanged payload. The complete test must naturally exercise
every conditional tool and all 22 profiles with meaningful, ready scope.

Include genuine plan choices, several semantic steering revisions at different
stages, multiple in-flight messages, same-task resume and a requested chronology.
Inspect each worker's first publication, prerequisite order and artifact binding.
A corrected call, premature audit or unexplained replay is not a clean run.
Source tests and a merely launched Desktop session never substitute for complete
native qualification.

Documentation mutations precede final verification. A no-impact branch requires
a finalized worker assessment, not a fabricated edit. Finish by presenting the
verified result and links, opening a fresh closure review, receiving the direct
close choice, and checking clean process completion. No initiative or alternate
legacy governance route exists. The ledger writes no coordinator data into the
product project; worker-owned product files are expected. Only verified plan and
report views are user-facing Markdown, while decisions and timeline remain
SQLite evidence.

Run the read-only preflight on a local or SSH host with:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/cortex-host-preflight.py
```

Before release, verify the exact allowlisted working-tree candidate, then verify
that the same required installable files are committed unchanged:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/verify-cortex-release.py --mode source
PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/verify-cortex-release.py --mode head
```

Source mode and installed-plugin checks are different evidence classes. Do not
claim installation, interactive model behavior, or published package state from
a source-only result. Record every unrun gate or environment limitation.

Unexpected MCP errors may be written to a private same-user diagnostic log.
Treat that log as sensitive: inspect only a bounded tail, extract sanitized
correlation metadata, and never paste raw records into chat, issues, prompts,
commits, or external systems.

The remaining publication requirements are documented in
[docs/release-readiness.md](docs/release-readiness.md).

For a separate test project, pass its canonical directory to `start` with
`--workdir PATH`; the launcher and candidate refresh remain rooted at this
repository's absolute `scripts/cortex-dev`.
The launcher temporarily enters the repository only for refresh/sync and
restores the selected workdir before starting ordinary Codex, so task project
root follows `--workdir`.

The live-smoke script is a dumb tmux transport only. It does not parse
readiness, trust, rollout state, sentinels, acceptance, approvals, MCP errors,
or retries; the coordinator/LLM decides those from the real attached or bounded
owner-only captured terminal. The output-only pipe never feeds input. Prompt
delivery is one literal normalized insertion followed by one standalone
`Enter` key; the distinct `enter` command is only an explicit key transport
after the operator has visibly observed the trust screen.
### Current live transport submission

The helper performs one literal normalized insertion, waits five seconds, and sends exactly one standalone named `Enter` key to the same exact pane. Receipts report transport delivery only; the coordinator/LLM confirms TUI acceptance from the pane and bounded events.
