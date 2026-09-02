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
        It preserves tasks, delegations, reports, governance assessments,
        initiatives, and closure records in a local ledger while leaving every
        orchestration and safe next-step decision to the model.
      </p>
      <p>
        <img src="https://img.shields.io/badge/Cortex-1.14.14-7c3aed" alt="Cortex 1.14.14" />
        <img src="https://img.shields.io/badge/Python-3.11%2B-3776ab" alt="Python 3.11+" />
        <img src="https://img.shields.io/badge/Codex-Desktop%20%7C%20CLI-111827" alt="Codex Desktop and CLI" />
        <img src="https://img.shields.io/badge/Ledger-SQLite%20schema%20v1-0f766e" alt="SQLite ledger schema v1" />
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
- [Required worker route: Codebase Memory MCP](#required-worker-route-codebase-memory-mcp)
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
> Configure Codex before the first Cortex 1.14.14 orchestration, then start a **new task**.
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
> **Cortex 1.14.14 ships an activation guard and a sanitized lifecycle
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
terminal consumption commits worker role and emits `tools/list_changed`; a
supporting client refreshes to only worker read/publication tools. A Desktop
client that retains the initial catalogue can still publish, while committed
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
| `$cortex:orchestrator <task>` | Start ordinary Cortex 1.14.14 coordination | `$cortex:orchestrator Find the race condition and fix it with tests` |
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
> source-backed knowledge baseline. Cortex 1.14.14 never blocks ordinary coordination
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
Their reports inform later delegations. Cortex does not impose a universal phase
order or a governance admission relation. When a plan or decision is relevant,
the coordinator preserves its exact lineage as declared worker evidence; it
does not make unrelated downstream work or closure unavailable.

#### Documentation impact is assessed after verified tasks

After worker-reported project verification, the coordinator makes a
report-grounded documentation-impact decision. Material behavior, architecture,
interface, command, verification, convention, or feature-ownership changes
require a documentation-sync worker and a separate documentation-verifier
worker. No impact requires a finalized worker-owned report with an explicit
English documentation-impact section and material/no-impact rationale; when
existing finalized reports do not contain that section, a bounded
evidence-synthesis worker submits it. The final initiative links that exact
report ref and closure evidence cites it; a self-asserted
`documentation_not_required` value is invalid.
The coordinator never submits a report on a worker's behalf. Documentation is
durable navigation, not runtime authority. Source, executable configuration,
schemas, and tests remain authoritative when prose drifts.

> [!CAUTION]
> Never place secrets, personal data, raw worker reports, private diagnostic
> logs, or credentials in generated documentation.

---

## Required worker route: Codebase Memory MCP

> [!WARNING]
>
> ### Install Codebase Memory MCP before project structural discovery
>
> **[DeusData/codebase-memory-mcp](https://github.com/DeusData/codebase-memory-mcp)**
> builds a local graph of functions, classes, calls, routes, and dependencies.
> Cortex workers can use it for architecture discovery, impact analysis, and
> end-to-end tracing, especially in large monorepos.
>
> Codebase Memory is a required worker MCP: every native worker must have it
> available and enabled before structural project-code discovery. If the MCP is
> missing or unusable, the worker stops with an environment blocker. Only an
> actual graph call that proves the indexed graph excludes the requested surface
> or is insufficient permits exactly one bounded ordinary-repository fallback,
> with the evidence-based rationale recorded. There is no silent or chained
> fallback. The coordinator is deliberately denied operational access; the
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

Codebase Memory is an evidence source and required worker host prerequisite,
not a Cortex ledger capability. Its absence blocks structural project discovery;
the worker must report that environment blocker without substituting ordinary
search. The ledger can still preserve the blocked evidence and support an
honest closure or final answer.

---

## How orchestration works

Cortex is more than a prompt asking Codex to “run several agents.” Cortex 1.14.14 combines
a durable local coordination ledger with model-owned orchestration and
governance. The root coordinator is a strict control plane: it delegates every
project action and every substantive domain analysis to workers, then reasons
only from user input, its bounded project-knowledge route, ledger state, and
immutable worker reports.

```mermaid
flowchart TB
    U(["User outcome, constraints,<br/>acceptance criteria, and language"]) --> ACT["Explicit activation<br/>user selects cortex:orchestrator<br/>ordinary complexity never activates Cortex"]
    ACT --> C0["Coordinator control plane<br/>orchestration and delegation only<br/>user communication follows user language"]
    C0 --> MODE{"Classify/revise C1 · C2 · C3<br/>then select governance depth<br/><b>minimal · light · full</b>"}
    MODE --> START["open_task — the only root-bearing call<br/>English objective + original request/language<br/>outcomes with linked acceptance · constraints<br/>stores canonical root; returns public task_ref"]

    subgraph MODEL["Coordinator lane — orchestration + bounded knowledge routing"]
        direction TB
        BOUNDARY["Never inspect source/code/config or perform project work<br/>Never edit · build · test · directly verify · do domain analysis"]
        START --> SPLIT{"Construct/follow evidence-driven DAG<br/>persist initiative revisions + delegation graph<br/>not a project solution plan"}
        SPLIT --> ROUTE["Closed direct-read routing exception<br/>applicable AGENTS.md + two indexes + selected pages<br/>already-known exact paths only<br/>no shell · search · graph · candidate probes"]
        ROUTE --> KC["Compile one contract per delegation<br/>documents · requirements · verification · ownership<br/>known doc state · further-discovery boundary"]
        KC --> D1["open_assignment A<br/>objective · role · textual scope · instructions"]
        KC --> D2["open_assignment B<br/>objective · role · textual scope · instructions"]
        KC --> DN["open_assignment …N<br/>parallel when scopes do not conflict"]

        D1 --- SCOPE["scope = required non-empty text boundary<br/>details belong in instructions · object scope is invalid"]
        D1 --- LANG["English worker plane: commentary · message · final · worker narrative<br/>canonical reports may carry one unchanged source_text value<br/>task/decision user text stays in labeled *_original fields"]
        D1 --- PROFILE["profile_name = exact packaged profile<br/>role = separate human label<br/>verify loaded proof or disclose degraded fallback"]

        D1 --> R1{"Choose exact model + effort"}
        D2 --> R2{"Choose exact model + effort"}
        DN --> RN{"Choose exact model + effort"}

        R1 --> X1["Luna: omit native model override<br/>Terra/Sol: pass exact model<br/>always pass exact reasoning_effort"]
        R2 --> X2["Independent per-delegation choice<br/>low · medium · high · xhigh · max"]
        RN --> XN["Profiles are advisory<br/>no backend routing or fallback ladder"]
        C0 --- BOUNDARY
    end

    subgraph WORKERS["Worker lane — all project action and domain analysis"]
        direction TB
        X1 --> W1["Worker A reads its brief<br/>canonical root + compiled knowledge contract"]
        X2 --> W2["Worker B consumes supplied contract<br/>does not independently redo routing"]
        XN --> WN["Worker N<br/>consume assignment evidence"]
        W1 --> A1["Discover · inspect · analyze<br/>edit · build · test as authorized"]
        W2 --> A2["Independent project action<br/>within its textual ownership boundary"]
        WN --> AN["Specialist · reviewer · writer<br/>or replacement work"]
        A1 --> P1["publish_result / publish_plan / publish_documentation<br/>one complete worker-owned publication<br/>opaque evidence; compact publication ref returned"]
        A2 --> P2["publish_result<br/>completed · blocked · failed"]
        AN --> P3["publish_result<br/>evidence + limitations"]
        WN -. "may end without report" .-> MISS["Evidence gap<br/>no lifecycle blocker"]

        VDELEGATE["Coordinator creates verifier delegation<br/>with exact model + effort"] --> VW["Independent verifier worker"]
        VW --> VCHECK["Run proportional checks, tests,<br/>falsification, review, or security analysis"]
        VCHECK --> VP["publish_result<br/>verification evidence + limitations"]

        PLANDEL["Coordinator creates planner delegation"] --> PW["Planner worker<br/>English project solution plan report"]
        PW --> PLANWRITE["publish_plan<br/>complete plan evidence<br/>stable publication ref · immutable digest"]
        PLANWRITE --> PLANPOLICY{"server-derived review policy<br/>minimal → informational<br/>light/full → required review"}

        DOCDEL["Coordinator creates documentation-sync delegation"] --> DW["Technical-writer worker<br/>update project + feature knowledge"]
        DW --> DOCWORK["Document material behavior · architecture · interfaces<br/>commands · verification · conventions · ownership"]
        DOCWORK --> DP["publish_documentation<br/>documentation change evidence"]
        DOCVERIFY["Coordinator creates documentation verifier"] --> DV["Independent documentation verifier worker"]
        DV --> DVCHECK["Check source grounding · links · commands<br/>Mermaid · scope · preserved user content"]
        DVCHECK --> DVP["publish_result<br/>documentation verification evidence"]

        NODOCDEL["Coordinator creates bounded no-doc<br/>evidence-synthesis/technical-writer delegation<br/>when a final documentation-impact report is needed"] --> NODOCW["Worker-owned English documentation-impact rationale<br/>report-grounded evidence when useful"]
        NODOCW --> NODOCP["publish_documentation<br/>finalized no-impact evidence"]
    end

    subgraph PROJECT["Target project/workspace — workers only"]
        direction LR
        FILES["Project files and source"]
        CMDS["Commands · builds · tests"]
        DOMAIN["Domain evidence · external tools<br/>subject to Codex/user approval"]
    end

    A1 -. "worker-owned access" .-> FILES
    A2 -. "worker-owned access" .-> CMDS
    AN -. "worker-owned access" .-> DOMAIN
    VCHECK -. "worker-owned verification" .-> FILES
    VCHECK -. "worker-owned verification" .-> CMDS
    FILES --- NOWRITE["Cortex writes no .codex directory,<br/>ledger, plan, report, or view under project_root"]

    P1 --> EVIDENCE["Coordinator consumes worker Summary + exact Report ref<br/>passes only relevant refs into later delegations"]
    P2 --> EVIDENCE
    P3 --> EVIDENCE
    MISS --> REWORK

    EVIDENCE --> REVIEW{"Evaluate evidence,<br/>assumptions, contradictions, and risk"}
    REVIEW -- "new material risk" --> REVISE["Append mode revision<br/>name new evidence and depth rationale"]
    REVISE --> SPLIT
    REVIEW -- "insufficient or failed evidence" --> REWORK["Create rework, specialist,<br/>or replacement delegation"]
    REWORK --> SPLIT
    REVIEW -- "real product/scope decision" --> ASK["Ask user in ordinary chat<br/>in the user's latest meaningful language"]
    ASK --> REVIEW
    P2 -- "blocked report or real worker question" --> QUESTION["Coordinator asks a complete localized question<br/>and records the user's exact original response"]
    QUESTION --> SAMEWORKER{"Same persisted worker is live and safe to resume?"}
    SAMEWORKER -- "yes" --> RESUME["followup_task to exact native_task_name<br/>decision + report refs copied byte-for-byte"]
    SAMEWORKER -- "no / ambiguous" --> REWORK
    RESUME --> W2
    REVIEW -- "verification is needed" --> VDELEGATE
    REVIEW -- "planning is useful" --> PLANDEL
    VP --> EVIDENCE
    PLANPOLICY -- "informational" --> EVIDENCE
    PLANWRITE -. "finalized plan report is predecessor" .-> D1
    PLANPOLICY -- "required or light/full review relation" --> PLANLINK["Read ready approval_view; publish exact plan revision<br/>and plans/current.md as a localized clickable link"]
    PLANLINK --> UDEC{"User decision<br/>approve · reject · request_revision<br/>clarification · cancel · accept_risk · override"}
    UDEC --> DECISION["narrow decision operation<br/>open clarification / plan review / steering<br/>then matching record operation with server binding"]
    DECISION -- "approve exact revision" --> EVIDENCE
    DECISION -- "revision requested" --> REWORK
    DECISION -- "clarification only" --> PLANLINK
    REVIEW -- "project verification reports satisfy acceptance" --> DOCIMPACT{"Assess documentation impact<br/>from worker reports only"}
    DOCIMPACT -- "material impact" --> DOCDEL
    DP --> DOCVERIFY
    DP -. "missing/failed evidence" .-> REWORK
    DVP --> DOCREADY["Coordinator consumes documentation handoff"]
    DVP -. "inadequate evidence" .-> REWORK
    DOCIMPACT -- "no material impact" --> NODOCEVID{"Finalized worker report already<br/>contains explicit no-doc rationale?"}
    NODOCEVID -- "yes" --> NODOC["Use finalized worker-owned report<br/>explicit English documentation-impact section<br/>no meaningless edit"]
    NODOCEVID -- "no" --> NODOCDEL
    NODOCP --> NODOC
    DOCREADY --> CLOSE{"Model-authored advisory verdict<br/>ready · ready_with_risks · not_ready"}
    NODOC --> NODOCINIT["private/internal initiative ledger helper<br/>exact task + documentation-impact report<br/>+ every other finalized evidence report"]
    NODOCINIT --> CLOSE
    CLOSE -. "best effort after evidence settles" .-> RECORD["close_task<br/>advisory task or initiative evidence<br/>cite exact evidence refs + digests"]
    RECORD -- "optional follow-up evidence" --> VERIFYCLOSE["read_task<br/>bounded task view<br/>verify links · subject · verdict"]
    VERIFYCLOSE -- "verified or disclosed limitation" --> FINAL(["User-facing final answer"])
    RECORD -. "closure write unavailable<br/>honest advisory limitation" .-> FINAL

    subgraph INIT["Project-level initiatives — model-owned program view"]
        direction LR
        I0["private/internal initiative ledger helper<br/>goal · risk · informational status"] --> IR["Append initiative revisions"]
        IR --> IL["Parent · dependency · task · report links"]
        IL --> IW["Unresolved/cyclic dependencies<br/>persist as warnings"]
        IW --> IC["Private/internal initiative closure<br/>may retain residual risk"]
    end
    START -. "link tasks across time" .-> I0
    EVIDENCE -. "link report evidence" .-> IL
    IC -. "program evidence" .-> REVIEW

    subgraph LEDGER["Durable Cortex 1.14.14 backend sidecar — storage and integrity only"]
        direction LR
        DB[("~/.codex/cortex/v12/projects/<br/>p-&lt;project-hash&gt;/cortex.db<br/>SQLite schema v1")]
        ROOT["open_task alone carries project_root<br/>saves canonical root + canonical task ID<br/>returns task_ref; no host-root inference"]
        ANCHOR["Public callers use compact typed refs only<br/>t_/d_/r_/u_/i_ (12 hex)<br/>canonical IDs remain non-callable DB evidence"]
        TL["Ordered task-scoped timeline<br/>tasks · assignments · publications · decisions · governance<br/>one-time derived backfill for retained 1.12.1 rows"]
        SAFE["Hard boundaries:<br/>schema/size · idempotency · compact-reference existence<br/>project isolation · transactions/FKs/uniqueness<br/>light/full plan → approval relation only"]
        NOGATE["No hidden workflow/lifecycle authority:<br/>no host stop event · profile capability · dependency warning<br/>or closure verdict chooses the next stage"]
        MAINT["Operator CLI outside the 14-tool MCP registry<br/>health · shard backup · checkpoint · optimize · vacuum<br/>offline restore · projection cleanup · backup retention"]
        ROOT --- DB --- ANCHOR --- TL --- SAFE --- NOGATE
        MAINT -. "task-ID anchor · exact confirmations<br/>restore only after MCP stopped" .-> DB
    end

    subgraph VIEWS["Host-private human-readable views — derived, never authority"]
        direction TB
        VIEWROOT["~/.codex/cortex/v12/projects/p-&lt;hash&gt;/tasks/&lt;task_ref&gt;/<br/>index · task · plans · delegations · reports · decisions · timeline"]
        JOBS["Canonical mutation + timeline + projection job commit atomically<br/>materialize afterward: best effort · dirs 0700 · files 0600"]
        VIEWSTATE{"human_view<br/>ready · stale · conflict · unavailable · disabled"}
        VERIFYVIEW["Publish only a ready returned absolute path<br/>regular/no-symlink · digest + source sequence current<br/>[localized readable label](&lt;exact returned path&gt;)"]
        FALLBACK["No current view: disclose in user language<br/>summarize canonical SQLite evidence inline · keep working"]
        JOBS --> VIEWROOT --> VIEWSTATE
        VIEWSTATE -- "verified ready" --> VERIFYVIEW
        VIEWSTATE -- "not ready" --> FALLBACK
    end

    START -. "durable write" .-> DB
    D1 -. "durable write" .-> TL
    D2 -. "durable write" .-> TL
    P1 -. "immutable report" .-> TL
    P2 -. "immutable report" .-> TL
    P3 -. "immutable report" .-> TL
    DP -. "immutable report" .-> TL
    DVP -. "immutable report" .-> TL
    RECORD -. "append-only task or private/internal initiative closure" .-> TL
    DECISION -. "append-only user evidence" .-> TL
    IR -. "revision history" .-> TL
    NOGATE -. "cannot prohibit safe next step" .-> C0
    TL -. "enqueue after canonical write" .-> JOBS
    VERIFYVIEW -. "plan · progress · report · decision · final links<br/>always with localized summary" .-> C0
    FALLBACK -. "projection failure is nonblocking" .-> C0
        ERROR["All 14 semantic tools: successful structuredContent<br/>Caller-correctable failure: isError + concise actionable text<br/>no raw diagnostics or report content"] -. "MCP response contract" .-> C0

    classDef user fill:#ede9fe,stroke:#7c3aed,color:#1f1638,stroke-width:2px;
    classDef model fill:#eef2ff,stroke:#4f46e5,color:#111827,stroke-width:1.5px;
    classDef worker fill:#ecfeff,stroke:#0891b2,color:#083344,stroke-width:1.5px;
    classDef evidence fill:#ecfdf5,stroke:#059669,color:#052e16,stroke-width:1.5px;
    classDef governance fill:#fff7ed,stroke:#ea580c,color:#431407,stroke-width:1.5px;
    classDef ledger fill:#f8fafc,stroke:#475569,color:#0f172a,stroke-width:1.5px;

    class U,FINAL user;
    class ACT,C0,BOUNDARY,SPLIT,ROUTE,KC,D1,D2,DN,SCOPE,LANG,PROFILE,R1,R2,RN,X1,X2,XN,REWORK,ASK,QUESTION,SAMEWORKER,RESUME,VDELEGATE,PLANDEL,PLANLINK,DOCDEL,DOCVERIFY,DOCIMPACT,DOCREADY,NODOC,NODOCDEL,NODOCINIT model;
    class W1,W2,WN,A1,A2,AN,P1,P2,P3,MISS,VW,VCHECK,VP,PW,PLANWRITE,DW,DOCWORK,DP,DV,DVCHECK,DVP,NODOCW,NODOCP worker;
    class FILES,CMDS,DOMAIN,NOWRITE,EVIDENCE,REVIEW evidence;
    class MODE,START,REVISE,PLANPOLICY,UDEC,DECISION,CLOSE,RECORD,VERIFYCLOSE,I0,IR,IL,IW,IC governance;
    class ROOT,ANCHOR,DB,TL,SAFE,NOGATE,MAINT,VIEWROOT,JOBS,VIEWSTATE,VERIFYVIEW,FALLBACK,ERROR ledger;
```

### The coordinator-only boundary

The root coordinator orchestrates; it does not perform the project task. This
is a permanent Cortex 1.14.14 invariant, independent of governance mode, task size, or
worker availability.

| Coordinator may | Coordinator must delegate to workers |
| --- | --- |
| Define the outcome, acceptance criteria, constraints, verification needs, and a dynamic orchestration DAG | Project discovery, source/code/config searches or reads, domain analysis, and the project solution plan |
| Direct-read the host-injected `AGENTS.md` context already supplied for the task, then the two project/feature indexes and only task-relevant linked pages | Shell/commands, `rg`/`find`/globs/graph/source/repository search, candidate probes, arbitrary documentation scanning, unrelated-link traversal, and documentation editing |
| Select or revise governance and create/inspect ledger records | Creating or editing project files and implementing substantive work |
| Create delegations, choose each worker's exact model/effort, enforce the recorded light/full plan-review relation, and coordinate native workers | Project commands, builds, tests, browser checks, external research, and every verification action |
| Read bounded report evidence, record coordinator-attributed user decisions, decide rework/replacement, and record advisory closure | Any attempt to fill an evidence gap by independently inspecting or testing the target project |
| Publish verified current host-private task/plan/report/decision links with concise localized summaries | Publishing a bare, stale, conflicted, unavailable, or unverified artifact path |

The bounded route exists only to compile the exact knowledge requirements for
each delegation. The bundled `orchestrator` skill is the single authority for
the route and six-part contract; profiles consume the supplied contract and do
not reconstruct it. Otherwise the coordinator reasons from the user's request,
Cortex records, and worker reports. Missing or inadequate evidence creates a
focused follow-up, review, verification, rework, or replacement delegation,
never coordinator-side project investigation.

The route is a closed read allowlist, not project-tool authority. Every
coordinator read uses a non-shell direct reader after the exact path is already
known. Unknown roots or paths and unavailable direct reads require a native
discovery/retrieval worker. Root discovery and every project-local state or
artifact check—including Git, manifests, caches, worktrees, existence/absence
or unchanged-state, and `.codex`—are worker-owned even when read-only, pre-plan,
report-recovery, or explicitly requested from the coordinator.

### Governance resolution

The coordinator model classifies and may revise C1/C2/C3, then selects
`minimal`, `light`, or `full`, records the
assessment, and may revise it when new evidence changes the appropriate depth.
The backend stores each revision; it does not classify, promote, or veto the
model's selection.

| Request and context | Effective mode | Model responsibility |
| --- | --- | --- |
| C1: bounded, low-risk, single-scope task | `minimal` by default | Define outcome and acceptance, delegate proportional verification, summarize reported unresolved items |
| C2: multi-step, cross-component, user-visible, ambiguous-acceptance, or substantial code work | `light` | Build only the worker-owned-stage DAG; obtain an approved planner report before delivery; track risks and verification evidence |
| C3: security, privacy, authentication, financial, destructive, production-critical, multi-repository, multi-task, or long-lived initiative work | `full` | Build only the worker-owned-stage DAG; delegate planning, falsification, independent review, and detailed closure evidence |
| Explicit user override | User-selected mode | Preserve `source=user_override`; record a concise risk warning when appropriate and continue safe work |

The C-level and selected depth are model-owned reasoning. For `light` and
`full`, the ledger additionally preserves one narrow, monotonic relation: a
completed planner report and the user's explicit approval of that exact ready
revision must be present before downstream delegation. It still does not choose
stages, profiles, models, efforts, acceptance, rework, or closure. Open
initiatives, dependency warnings, absent reports, missing closure, and a
`not_ready` verdict do not otherwise disable safe coordination tools; schema,
compact-reference, project-isolation, approval, and filesystem boundaries still
apply.

### The orchestration cycle

1. **Explicit activation and first-call route.** Cortex starts only after the
   user selects `cortex:orchestrator`. The model defines the outcome,
   observable acceptance criteria, material constraints, and proportional
   verification needs, then opens the durable task before any project execution
   action. A prose acknowledgement, shell/repository inspection, project-state
   check, or worker dispatch before task opening is a route violation.
2. **Durable task and mode.** `open_task` records the English-normalized
   objective, verbatim original request, user language, and bounded task
   contract. Each independent outcome has a non-empty requirement and its own
   linked acceptance criteria; constraints remain linked metadata, and no
   verification obligation is synthesized by copying acceptance. Optional
   `context` never substitutes for an outcome.
   After `open_task` and before the first assignment, the coordinator must call
   `assess_governance` to append one evidence-backed model or
   user-override assessment.
3. **Dynamic DAG and bounded routing.** The coordinator builds only the
   orchestration DAG, never a project solution plan, and delegates all
   project discovery, inspection, domain analysis, implementation,
   documentation, and verification. Its only target-project read exception is
   the orchestrator-owned route through the host-injected `AGENTS.md` context,
   the two project/feature indexes, and only task-relevant linked pages. It runs
   independent scopes concurrently when beneficial. Planning is optional for
   genuinely `minimal` work. For `light`/`full`, a `planner` stage publishes the
   immutable required-review plan and explicit approval of that exact current
   plan is required before delivery. This is a narrow backend admission
   invariant, not a scheduler: planning/evidence assignments and unrelated safe
   coordination remain available.
4. **Precise dispatch.** The coordinator provides rich six-part knowledge
   guidance for each delegation in `instructions`; the native brief is only a
   compact bootstrap. Full common policy, profile guidance, task evidence, and
   assignment scope arrive from the mandatory first assignment read.
   Every delegation also records its objective, separate human-readable `role`,
   exact packaged `profile_name`, required concise textual `scope`, finalized
   compact input report/decision refs, persisted server-derived native task
   name, and exact model/effort pair. The successful creation response carries
   one closed compact `native_dispatch` plus replay state; that projection
   preserves the exact effort, omits the model only for default Luna, and is forwarded unchanged to the
   active native spawn operation.
   Detailed execution belongs in `instructions`; an object-shaped `scope` is
   invalid. The coordinator—not the backend—chooses profile, model, and effort.
   The successful receipt supplies one literal native dispatch; the
   coordinator forwards it once to the active host spawn operation and waits for
   that worker's report. It never
   reassembles an ad-hoc prompt or reuses one worker across delegations.
5. **Plan review when needed.** A planner publishes an English `plan` report.
   A requested or necessary main plan always receives a verified exact revision
   and host-private Markdown link with localized approve/revise/reject/cancel
   input. The coordinator records the exact-revision approval through
   the matching narrow decision record operation; implementation or research beyond discovery/planning
   receives that decision ref before dispatch. For light/full delivery this
   relation is always required.
   The ready-view relation is validated for the specific approval request, but
   the backend enforces only the exact light/full delivery admission relation;
   a revised plan gets a new digest and never inherits approval. A plan with an unresolved product,
   policy, scope, requirement, or acceptance choice cannot enter generic review;
   it first needs an explicit clarification and a lossless revised plan.
6. **Immutable evidence with strict ownership.** Workers alone call the
   applicable `publish_plan`, `publish_result`, or `publish_documentation`
   operation for their assignment. The coordinator creates the
   delegation, dispatches its exact rendered brief, waits, and consumes the
   worker's concise native handoff plus exact server-returned publication ref;
   the native handoff is routing context only. Before synthesis, revision,
   rework, closure, or the final answer, it consumes the relevant canonical
   report bodies through `read_task` evidence mode to completion, and it never
   submits on behalf of a worker. Every publication is complete and immutable;
   a confirmed successful publication ends that worker's tool activity, so it
   emits its compact native handoff without a second publication call;
   identical reconciliation is reserved for an actually ambiguous transport result.
   every compact ref and digest is copied byte-for-byte from a successful result or inspection,
   never parsed, reconstructed, normalized, or suffixed.
7. **Evidence-driven adaptation.** The coordinator loads `adaptive-pipeline`
   after a material report, decision, failed/incomplete check, changed risk,
   contradiction, scope change, or documentation finding. It reads only the
   bounded evidence needed, then may add, remove, reorder, retry, or
   parent-link rework worker stages and appends the resulting pipeline revision
   through the task timeline and, only for cross-task or long-lived governance,
   a task-linked initiative graph; ordinary stage/rework notes do not revise an
   initiative. Completed reports remain immutable. It never reopens project
   artifacts, reruns checks, or writes the project plan.
   The standard Codex To-Do projection mirrors only current pipeline stages and
   review state; it is refreshed whenever either changes and never becomes a
   worker-subtask checklist or report-body mirror. Worker handoff summaries
   carry the current stage/state, outcome, next owner/action, pipeline/review
   delta, changed or verified surface, exact report ref/digest, and residual
   risk or unrun checks, so routine coordinator report-body reads are not
   needed.
8. **Project initiatives when useful.** Multiple tasks with a shared long-lived
   goal, risk, milestone, or dependency can be linked through an initiative.
   The model owns its status, relationships, and interpretation.
9. **Conditional documentation stage.** After project verification, the
   coordinator assesses documentation impact from worker reports only. Material
   changes to behavior, architecture, interfaces, commands, verification,
   conventions, or feature ownership require a delegated
   `documentation-sync` update to project/feature knowledge followed by a
   separate delegated documentation verification. When there is no material
   impact, require a finalized worker-owned report containing an explicit
   English documentation-impact section and material/no-impact rationale, and
   do not create meaningless edits. If the existing reports do not contain that
   section, create one bounded
   evidence-synthesis/documentation-impact worker and wait for its finalized
   publication. The coordinator may use its bounded routing reads to identify
   owned knowledge paths, but never edits or verifies the documentation itself
   and never publishes worker evidence.
10. **Advisory close and active publication.** After that conditional
   documentation stage and sufficient finalized worker evidence, the model
   selects the advisory verdict `ready`, `ready_with_risks`, or `not_ready` and
   automatically attempts `close_task`, followed by bounded
   inspection of the intended record. The no-impact route can create or update
   an initiative with the exact task relationship, the exact
   documentation-impact report ref, and every other required report link; cite
   those refs and returned digests in closure evidence; and inspect task and
   initiative governance. A separate task closure may be recorded when a
   distinct task verdict helps, but neither closure is a lifecycle gate and
   `ready_with_risks` never asks the user for confirmation. The separate
   `execution_outcome` projection contains `evidence_status`,
   `finalized_report_count`, `completed_report_count`, `effective_revision`,
   `coverage_status`, and `outcome`. It derives deterministically from current
   effective-contract coverage, excluding historical/superseded claims and
   report arrival order. This evidence makes no native-lifecycle claim and
   remains unchanged by advisory bookkeeping.
   The closure mutation response also returns the current `conformance_review`
   projection, so a recorded advisory verdict cannot be mistaken for evidence
   readiness. The ledger never upgrades a requested verdict and normalizes an
   overstated request downward; callers communicate the recorded value.
   `close_task` makes at most one server-owned replay reconciliation for a
   verified transient persistence or inspection failure. If confirmation still
   cannot be established, it returns
   `closure_confirmation.inspection_status`=`unconfirmed` with a reason such as
   `persistence_unavailable` or `inspection_unavailable`; this discloses
   advisory uncertainty without changing neutral finalized-report evidence. The
   model gives the user a
   localized final answer with verified host-private links and concise
   summaries, disclosing any closure or projection limitation and summarizing
   canonical SQLite evidence inline.

### Cortex 1.14.14 delegation and publication protocol

A worker assignment is a durable, model-authored work record. Its required `scope` is
a non-empty text string of at most 65,536 characters containing a concise
boundary of worker ownership; detailed execution belongs in `instructions`, and
object-valued scopes are invalid. The mission's explicit `responsibility`
selects planning, delivery ownership, or non-owning evidence independently of
the packaged `profile_name`. When one assignment covers the complete current
scope for its responsibility, `outcomes` is omitted: the server binds all
advertised `delivery_outcomes`, all `evidence_outcomes`, or every current item
for planning atomically, eliminating unnecessary model-side copying. The field
is supplied only for an intentional delivery/evidence partition and then must
be one exact non-empty subset of the matching advertised list. Prose item names
never substitute for exact current names where a partition is required. The
state projection publishes that canonical
`aggregate_coverage.assignment_scope`. When completed owner evidence makes
`terminal_rework=steering_revision_required`, a user-confirmed steering revision
must create new delivery outcomes before any corrective delivery assignment.
`open_assignment` returns only one compact closed
`native_dispatch` plus a replay flag. It carries isolated-history behavior, the
compact assignment anchor, native task name, exact effort, and an explicit
model only for non-default Terra or Sol;
Codex forwards it unchanged to the active host spawn operation. The
coordinator selects one exact packaged `profile_name` and verifies its loaded
proof; the separate `role` is a bounded human-readable assignment label, never
profile proof. An unavailable fallback is limited to a degraded non-durable
dispatch with a complete role contract plus explicit disclosure. The worker's
first semantic operation is `read_task(view=assignment)`; it then performs its
bounded work and publishes immutable evidence using its worker-scoped `task_ref`.
The assignment response places a compact publication-reconciliation block with
the exact public outcome names in the first `TextContent` block, ahead of the
larger policy, contract, and predecessor-evidence body. The same block remains
first when the full serialized result is too large to duplicate and the host
must use `structuredContent`; it is a projection of that structured result, not
a second authority. When the complete MCP envelope fits, the full structured
result is still duplicated as the final JSON text block for compatibility.
Predecessor evidence uses server-owned pagination. A worker continues
only immediately after an otherwise-identical read returns `has_more=true`;
after the terminal page it proceeds to work and one publication instead of
rereading the assignment. An identical restarted page reconciles the durable
consumption receipt without adding a second receipt or timeline event.
Every successful non-terminal page retains the original host lifecycle claim
for the same bound child and persistent connection; the continuation never
mints a second claim, and publication remains unavailable until the terminal
page.
Large assignment authority is likewise emitted as ordered path fragments before
predecessor evidence. Page positions and continuations remain server-private,
UTF-8 text is segmented only at valid character boundaries, and worker
publication authority is established only after the terminal assignment page.
The worker owns every publication call; the coordinator never submits plan,
result, verification, synthesis, or documentation-impact evidence on its behalf.
There is no separate public evidence-consumption operation. Neither the public
assignment receipt nor the evidence result proves native
termination or acceptance.

Private task, assignment, report, decision, digest, and cursor identities remain
opaque ledger data and are not callable through the public facade. The sole
public identifier is the exact Cortex-issued `task_ref`; copy it unchanged from
a successful result or server-rendered worker bootstrap and never parse,
reconstruct, normalize, or reformat it.

`publish_plan`, `publish_result`, and `publish_documentation` are the three
worker-owned publication operations. Each records one complete publication for
the worker's assignment and returns only a task-scoped `task_ref/state/replayed`
receipt. The
server validates the publication against the assignment and declared evidence;
the coordinator never publishes on a worker's behalf. Coverage is stored as
one disposition per assigned item. If a worker mechanically repeats the same
item with the same status, the server preserves every unique verification fact
and coalesces those rows before digesting the canonical report; a conflicting
status, omitted item, or foreign item is rejected without consuming the
publication slot. Exact retries are idempotent, while changed payloads conflict
without a second mutation.

Coordinator and worker audiences are monotonic for each MCP connection. A
coordinator connection cannot consume or publish worker authority, and a
worker connection cannot acquire coordinator authority. The host lifecycle
receipt binds the exact native child to one assignment using only owner-private
digests; it stores no task locator, worker locator, assignment body, or native
message plaintext. A copied worker-scoped `task_ref` is therefore never bearer
authority on a new process or connection.

The current plan publication contract checks observable evidence, explicit
stage ownership and dependencies, verification, residual risks, unresolved
items, and documentation impact before accepting a terminal plan publication.
The server derives the planner's complete scope from the current effective
contract and supplies it through the assignment brief; the coordinator neither
selects nor recopies planner outcome names and uses only the public receipt;
after publication the coordinator uses `read_task` to consume
the server-produced evidence view and does not reconstruct report bodies or
acceptance state.

Publication payloads are bounded and validated as complete structured evidence
before persistence. The public API intentionally has no separate report
assembly or report-body operation: workers submit the appropriate publication,
the coordinator consumes bounded evidence through `read_task`, and later
workers receive the selected predecessor evidence inside the server-rendered
assignment bootstrap.

The complete compact UTF-8 JSON argument object for every operation has an
advertised 65,536-byte aggregate bound independent of per-field limits. For
`publish_result`, a root aggregate-size failure reports only bounded numeric
actual/maximum sizes and safe advertised-section contributions. It does not
invent a field name or include handle-reuse guidance. The worker may make one
materially smaller, schema-complete correction that preserves every required
coverage row, change, verification state, risk, unresolved item,
documentation-impact conclusion, and status. Unchanged, ellipsized, truncated,
incomplete, or still-oversize corrections stop; Cortex never silently trims
evidence or creates a publication before validation succeeds.

Later assignments receive only the relevant finalized publication evidence and
decision context selected by the server-side report policy. Worker evidence
remains immutable, and contradictory, blocked, or incomplete results remain
visible rather than being flattened into a server-owned verdict. When a broad
policy selects reports from several independent authors, Cortex preserves all
of them and leaves the optional single-predecessor relation unset; it never
blames or exposes private report-reference fields that are absent from the
public assignment schema.

The active host schema, not the ledger, defines native spawn and follow-up
arguments. There is no backend-enforced fixed `wait_agent`/read/continue sequence and no
`SubagentStop` barrier. Native waiting is advisory host coordination outside
the ledger; durable task state and finalized publication evidence are completion
authority. After every bounded native wait returns—including timeout, an empty
result, or a contradiction with visible child completion—the coordinator reads
current task state or relevant evidence before deciding whether to wait again.
If publication already exists, it consumes that evidence and continues without
another wait for the same child. If no publication exists and the child remains
active, another bounded wait is optional. Lifecycle stop without publication
uses the explicit loss/recovery route: disclose the evidence gap and replace the
same delivery owner only after recording an explicit blocked/aborted reason and
non-empty evidence. The coordinator never remains in model-only waiting after a
wait call has returned and never lets empty host output hide durable evidence.
Repeated waits, elapsed time, slow progress, or an absent publication do not
authorize interruption of an active child. Recovery begins only after a
host-confirmed terminal stop without publication; broad report evidence cannot
replace the exact lost predecessor derived from the selected outcomes.

### Cortex 1.14.14 plans and user decisions

A plan is a complete `publish_plan` publication with private canonical
content/manifest identity. The public call does not choose review policy: the
server derives persisted `informational` for minimal governance and `required`
for light/full governance, and binds any predecessor relation privately. For
light/full delivery, the policy is therefore `required`; the
coordinator opens review, presents the verified plan, and records an explicit
decision before dispatch. Derived review states distinguish informational,
awaiting user, approved, revision requested, rejected, cancelled, and superseded
plans. A plan is current only for the effective-contract revision captured by
its planning assignment. Any material steering revision makes earlier plans
and their approvals historical; `active_plan`, plan review, and light/full
delivery remain unavailable until a new plan for the new revision is published
and explicitly approved.

`record_clarification`, `record_plan_review`, and `record_steering` append
coordinator-attributed `user_via_coordinator` evidence for a task. Their public
requests are task-ref-only: they carry the task reference, exact response and
language, and steering outcome changes where applicable. Private subject,
revision, digest, and decision bindings are resolved by the server. A plan
review additionally checks the current private ready view. Missing, extra,
renamed, or cross-mixed fields are validation errors; retry with the exact
advertised task-scoped shape rather than guessing an alias.

Steering additions are complete outcomes. Independent unpaired additions create
independent top-level contract items even when the task previously had only one
outcome; one retire plus one add is an atomic replacement. Assignment ownership
remains transactional and singular per current outcome. Delivery may proceed in
parallel across distinct current outcomes, while same, stale, overlapping, or
ambiguous ownership fails closed with bounded public classification and no
private ledger identity.

The record is evidence, not cryptographic proof that a particular human acted.
For a plan marked `required`, and for the active `light`/`full` relation, the
coordinator asks for explicit review before dependent work. Cortex checks the
task-scoped request against the private immutable plan relation; it does not
infer a human response or assess the plan text. Clarification is not approval,
and a revised plan requires a new decision. Inspection, evidence reads,
recovery, and safe planning work remain available; the narrow relation prevents
only downstream delegation that would bypass the required review.

### Cortex 1.14.14 evidence and briefing boundaries

The bundled `orchestrator` and `cortex-control` skills define coordinator
policy, safety, governance, evidence handoff, model routing, and the uniform
tool protocol. Advisory profiles add worker role guidance without becoming a
second runtime contract.

For planning-heavy work, pre-planner analysis is conditional. The coordinator
creates bounded read-only Luna evidence assignments only when material
uncertainty, separable repository questions, cross-domain dependencies, or
conflicting evidence would otherwise force the planner to repeat broad
discovery. Effort is chosen per question from the full advertised Luna range up
to `max`; it is not pinned globally. There is no arbitrary total worker cap:
useful non-duplicative assignments queue in the model-owned DAG and dispatch as
native slots become free. The planner consumes finalized evidence, fills only
remaining gaps, and remains the sole owner of one immutable plan.

The bundled `orchestrator` is the single authority for bounded knowledge
routing and its reusable per-delegation template. The host-injected
`AGENTS.md` context already governs the task; the coordinator does not reread a
global or project-root `AGENTS.md`. It reads `docs/project/index.md`,
`docs/features/index.md`, and task-relevant pages selected from those indexes.
It uses a non-shell direct reader only for already-known exact paths; shell,
commands, `rg`, `find`, globs, graph/source/repository search, directory listing,
and candidate probes are excluded. Unknown roots or paths are worker discovery.
The coordinator embeds one compiled six-part semantic contract in the
delegation `instructions`; the compact native bootstrap only directs the worker
to its mandatory first assignment read:

1. exact documents to consume first and why they apply;
2. only task-applicable requirements, with source paths;
3. exact verification commands, working directories, and evidence needs;
4. ownership constraints and mutation/external-action boundaries;
5. known missing, stale, conflicting, or incomplete documentation state;
6. whether further documentation discovery is authorized and, if so, its
   exact purpose, boundary, and stopping condition.

This is not another MCP request object. Profiles consume it as supplied input,
do not repeat the index path list, and never independently redo routing. A
missing or unreadable index produces a bounded discovery-worker delegation.
Worker-reported documentation drift is assessed for task impact; it does not
automatically activate harvest.

Only `open_task` accepts the exact resolved `project_root` and stores the
canonical project association. It returns a 14-character deterministic
`task_ref` (`t_` plus a 12-hex task suffix) for public task-anchored calls and
preserves the full `task_id` as durable evidence. Resolution scans only private
Cortex 1.14.14 shards and fails closed on zero or ambiguous matches.
Its optional arbitrary JSON `context` never supplies or overrides the root.
The seven task-anchored public tools use `task_ref` to locate and validate the
saved project ledger; historical full `task_id` locators remain direct-service
compatibility only. Initiative calls use the task only as a project anchor,
never as permission. The mandatory first assignment read carries the saved root
for working-directory context. MCP call metadata has no
guaranteed project-root binding, the plugin's stdio `cwd="."` is the plugin
directory rather than the target project, and Cortex 1.14.14 has no root-inference hook.

The task's operational `objective` is English-normalized. Before `open_task`,
the coordinator reads every available user-supplied attachment or pasted
specification and losslessly normalizes every decision-bearing detail—exact
limits, identifiers, handlers/fields, states, negative requirements, external
boundaries, edge cases, and verification expectations—into outcomes,
acceptance, constraints, and bounded context. Phrases such as “as specified”
never substitute for those details. `open_task` also
preserves exact `user_request_original`, `user_language`, and the
`cortex/task-contract/v3-outcome-linked` result contract: independent outcomes,
each outcome's linked acceptance criteria, and task constraints. Acceptance is
not copied into a standalone verification plan. Each public contract list is
bounded by its advertised live schema. The public call requires the
exact original request and a concrete language; compatibility defaults exist
only for already-retained older pre-release rows. Coordinator-to-user messages
follow the latest meaningful user language. Every native worker commentary,
update, inter-worker message, final response, tool-authored durable string,
ledger record, and generated-view operational source is English; acceptance
checks the entire child transcript, not only its final. Verbatim user language
appears only in labeled original fields. Decision contracts use neutral
`prompt`, exact `response_original`, and `user_language`; retired `prompt_en`
and `response_en` fields are rejected. Canonical product-facing report
and handoff payloads instead carry any needed user-authored source material
once in optional `source_text`, unchanged and without a language tag or
translated/original duplicate. A worker-authored product artifact may use
another language only when the task itself requires it.

The worker brief is coordination context, not lifecycle authority. Profiles do
not contain model literals or capability rules. Prompt volume targets are
advisory only; material user instructions, decisions, risks, report references,
or verification evidence must not be dropped merely to meet a target.

Tasks, assignments, publications, assessments, initiative revisions, and closures
retain ordered ledger sequences. `read_task` uses private server-owned
continuation for bounded incremental chronology. Coordinator state history is
returned in fixed 16-event pages, and only the result's top-level `has_more`
value controls whether the immediately following identical read continues;
`data` never exposes a competing cursor or pagination marker. Task and
assignment inspection expose compact publication references; publication
results remain bounded and are returned through their current tool contracts.
The current MCP schema owns
exact argument and response shapes.

Server-issued handles are copied byte-for-byte to the matching current
operation. They must never be converted, concatenated, or substituted.

Canonical compact decision references match `u_[0-9a-f]{12}`. They are opaque
evidence values: copy returned references exactly and never reconstruct them.

Retained 1.12.1 rows receive a one-time, conservative task-scoped chronology repair
on a normal store open when the 1.12.1 backfill marker is absent. It appends only
missing derived events—such as `report_chunk_appended` and unambiguously linked
initiative/closure history—without rewriting an existing timeline row or report
body. Every repaired payload carries a bounded `backfill` marker; ambiguous
report-only lineage is warned about rather than guessed. The repair transaction
also queues a best-effort refresh of the affected host-private views.

### Cortex 1.14.14 host-private human-readable views

SQLite remains the sole canonical store, but Cortex 1.14.14 projects current task state to
private Markdown beside the database:

```text
~/.codex/cortex/v12/projects/p-<project-hash>/
├── cortex.db
└── tasks/<task_ref>/
    ├── plans/current.md
    ├── plans/revisions/<plan-report-id>.md
    └── reports/<report-id>.md
```

The compact `task_ref` (`t_<12-hex>`) is the readable directory name. Full
canonical task IDs remain in SQLite and rendered evidence, never in a
user-facing projection link path. Existing released `tasks/<task-id>/`
directories migrate lazily with an atomic no-replace rename; a competing compact
directory is preserved and reported as a non-ready conflict.

Only finalized reports and plans are materialized. They are human-readable
documents with a title, state, labeled headings, normal lists, paragraphs, and
report sections; they are not raw nested field dumps and do not expose task IDs,
timeline history, delegation details, decision records, or other SQLite ledger
data. The renderer owns the document hierarchy: ordinary caller-authored
strings are treated as data and sanitized context-sensitively so headings,
lists, tables, blockquotes, HTML, rules, and fences cannot inject structure.
Readable punctuation is retained; only explicitly typed blocks (such as a
code block) emit their intended formatting. An optional
`cortex/report-view/v1` envelope is parsed only at render time; malformed,
unknown, or legacy content uses the safe generic fallback and never changes
report submission or persistence. When a ready view is useful, the coordinator publishes only the
exact returned report or plan path as a localized clickable Markdown link, for
example `[Открыть план](</absolute/path/to/t_ref/plans/current.md>)`; it never
constructs, wraps, or line-breaks that destination.

This release creates no `.codex` directory, database, plan, report, view, or ignore rule
under `project_root`. Markdown is disposable and never parsed back into SQLite.
Task, delegation, decision, initiative, closure, governance, handoff, index,
and timeline records remain SQLite-only; none has a user-facing Markdown view.

The canonical mutation, timeline event, and private projection job commit in
one SQLite transaction. Materialization runs afterward as a nonblocking,
best-effort operation using same-directory temporary files, `fsync`, atomic
replace, parent-directory `fsync`, and read-back digest verification. Directly
altered generated files are preserved as `conflict`; they are never silently
overwritten. Per-target serialization, leases, and newer-sequence supersession
keep concurrent writers bounded. Directories use `0700`; Markdown uses `0600`.

Every mutation/read may expose a dynamic `human_view` state: `ready`, `stale`,
`conflict`, `unavailable`, or `disabled`. An absolute `path` is returned only
for `ready`, after no-symlink/regular-file, digest, task-subtree, and current
source-sequence checks. The coordinator actively links verified views—with a
concise localized summary—during plan review, progress/status, report
acceptance or rework, user decisions, and the final answer. It never publishes
a bare or stale path. If a view is not ready, it says so in the user's language
and summarizes canonical ledger evidence inline without delaying safe work.

The full projection layout, tamper behavior, publication contract, and
verification scenarios are documented in
[human-readable task views](docs/features/human-readable-task-views/index.md).

### Cortex 1.14.14 model-owned rework and recovery

The server owns no recovery state machine and no Luna → Terra → Sol escalation.
After new or failed evidence, the coordinator chooses whether to create rework,
replace a worker, select a different specialist, request user input, or finish
with disclosed limitations. It does not inspect the project to repair or
validate a failed report. Any replacement receives a freshly selected exact
model and reasoning effort.

Every coordinator state read binds each exact current semantic outcome to its
coverage `status`/`reason`, `ownership`, and `delivery_assignability`. Ordinary
delivery selects only `assignable` outcomes. A terminal owner's outcome is
`not_assignable_terminal_owner` and remains immutable evidence; a nonterminal
owner is `loss_recovery_only` and can move only through the confirmed-loss path
below. The mapping is outcome-keyed and remains stable when steering adds,
retires, or reorders outcomes; coordinators never infer identity from aggregate
row position. Mixed owned/new requests still fail closed without mutation.

A confirmed lost nonterminal delivery worker is replaced only through
`open_assignment.loss_recovery`. The coordinator supplies `blocked` or
`aborted`, a concrete reason, and non-empty evidence; Cortex derives one unique
current predecessor from the exact selected outcomes, records immutable loss
evidence, stales its worker lease, and links the successor in the same SQLite
transaction. Lease expiry, reconnect, silence, copied locators, report
references, or bare assignment references never infer loss or authorize a
replacement.

A failed or partial QA report, any failed executed check, and any required
unrun check now keep the verification stage in `rework_required`. Source
failures go to implementation ownership; candidate stamp, dependency, CI,
provenance, and test-harness failures go to release or verification-
infrastructure ownership. An independent verifier must rerun the failed and
affected gates after correction. A passing focused subset or unrelated later
work cannot silently supersede unresolved QA evidence.

Before each rework assignment, the coordinator also preflights the mission's
explicit responsibility and exact item scope against current governance and
predecessor evidence; the selected profile supplies expertise only. Light/full production-owner work requires finalized approved planner
evidence from the same current effective-contract revision; bounded C1 owner work that genuinely needs no plan stays minimal from
the outset, while test-only QA correction remains non-owning. Multiple workers
or rework alone do not justify light/full governance. A rejected first attempt
remains a failed orchestration check even if a later retry succeeds.

A `not_ready` closure is an advisory recommendation. It can be followed by a
new delegation immediately. Missing closure, missing worker report, unresolved
dependency, or failed ledger write does not prohibit the next safe meaningful
step. The model should disclose only material missing evidence and residual
risk.

### Cortex 1.14.14 closed public response boundary

Every tool has a closed input schema in the active MCP registry. Each
audience-projected `tools/list` response is kept below 65,536 bytes. The
bundled MCP is required at session startup and its catalogue is excluded from
deferred discovery, so Desktop must expose the complete direct catalogue before
the first model turn instead of silently hiding `open_task`. Family-specific
successful-result schemas remain internal runtime contracts and are not
duplicated into the optional MCP `outputSchema` field. The runtime filters unrelated canonical
handles before validating every successful service result against the full
internal schema. A successful MCP tool result carries the same canonical data
in `structuredContent` and serialized JSON `TextContent`, with `isError=false`,
whenever the complete duplicated response fits the bounded physical frame.
This makes a successful one-shot worker assignment self-contained for hosts
that retain structured data in events but expose only text to the model.
Only a genuinely non-duplicable oversized result uses the fixed
`structuredContent` notice; bounded worker authority is paginated so its first
successful consumption never depends on replaying an already consumed link.
Multi-report assignment evidence uses its dedicated 224 KiB response envelope,
while each stored JSON value retains the separate 65,536-byte bound. Crossing
the storage-value size during response assembly is not invalid content: the
complete valid handoff is returned within the response envelope or continued
through the same server-owned assignment read.

Each advertised tool description mechanically includes the exact ordered set
of required input properties from that tool's closed `inputSchema`, together
with an instruction to verify the complete set before invocation. The schema
remains authoritative; the description is generated from it so the two cannot
silently drift. If a caller still omits multiple required properties, runtime
validation reports the complete bounded missing-property list in
`structuredContent.error.details.missing_fields` and in the recovery action,
allowing one corrected call instead of serial trial-and-error calls.

Caller-correctable failures instead return `isError=true`, one bounded,
sanitized text-content explanation, and a matching sanitized
`structuredContent.error` object with a stable Cortex code, details, and next
action. The error object is not a successful result-schema variant and never
contains task/report content, credentials, raw diagnostics, filesystem state,
or private ledger records. Server-state faults use a sanitized JSON-RPC
internal error rather than a second incompatible tool-error shape. No error may
include task/report content, credentials, raw diagnostics, filesystem state, or
private ledger records.

Public mutations derive private idempotency and lineage from the active
task/assignment context. An identical publication reconciles with
`replayed=true`; a changed payload for the same logical publication returns a
non-mutating conflict.

Core coordination calls validate shapes, sizes, enumerations, compact-reference
existence, and project isolation. Initiative status, dependency warnings,
closure verdict, and documentation findings do not choose the next safe
operation. Governance assessment remains coordinator-owned, while backend
admission requires an assessment before every assignment and, for light/full
delivery, an approved current plan whose immutable identity is derived from
ledger state rather than supplied by the caller.

### Cortex 1.14.14 public API and audience boundary

The private registry contains exactly fourteen tools. `tools/list` exposes an
immutable audience projection: coordinator connections receive coordinator
operations plus `read_task`; signed worker-candidate and committed worker
connections receive only `read_task`, `publish_plan`, `publish_result`, and
`publish_documentation`.
`open_task` is the sole explicit root boundary. Task-anchored operations use
the server-issued task anchor; `open_assignment` creates one worker assignment
and returns its server-rendered bootstrap. The three worker-owned publication
operations also accept the worker-scoped `task_ref`; private assignment and
continuation identity is derived from the exact assignment evidence consumed
by that worker on the exact host-bound connection. A fresh or replacement
connection cannot recover consumed worker publication authority from a copied
worker-scoped locator, report reference, durable continuation, or assignment
reference. Never-consumed, already-consumed, foreign, stale, partially bound,
or mismatched relations fail closed outside their original connection. No
public operation accepts canonical `task_id`,
assignment IDs, report IDs, digests, or cursors, and no public operation infers
a root from host metadata, thread identity, or process working directory.

| Tool | Contract |
| --- | --- |
| `open_task` / `read_task` | Open a task and read bounded state, assignment, or evidence views with server-owned continuation. |
| `open_clarification` / `record_clarification` | Open one server-owned clarification binding and consume it with the matching user response. |
| `open_plan_review` / `record_plan_review` | Open one server-owned plan-review binding and consume it with the matching review outcome. |
| `open_steering` / `record_steering` | Open one server-owned steering binding and consume it with the matching contract change. |
| `open_assignment` | Open one worker assignment and return the server-rendered worker bootstrap. |
| `publish_plan` / `publish_result` / `publish_documentation` | Publish worker-owned evidence for planning, implementation/verification, and documentation impact. |
| `assess_governance` | Record an advisory governance assessment. |
| `close_task` | Record the final advisory closure aggregate from durable evidence. |

The server independently authorizes every call against that immutable audience;
tool discovery is not the security boundary. Lifecycle hooks provide host-side
activation ordering, a signed one-shot child attestation, and bounded
observation; they do not grant ledger authority or authorize project work. The server validates the exact
packaged `profile_name` and projects one compact closed native dispatch
statelessly, but never spawns or authorizes the native worker. Native spawn
input remains host-owned and is never rewritten through `PreToolUse.updatedInput`.
The validated unchanged native spawn creates only session-isolated pending
correlation. It does not select an MCP audience. `SubagentStart` creates a
one-shot worker-candidate attestation bound to the exact child
agent/session/assignment using sanitized private digests. A connection that
initialized before this child attestation may adopt it only while its role is
still unknown and only after the exact `PreToolUse` authorization is signed;
request content alone cannot change its audience, and a committed coordinator
role is irreversible. Inherited root environment is never treated as exact
child identity. The child's exact first
`PreToolUse(read_task)` lifecycle event then signs a one-shot call
authorization bound to child agent, turn, session, assignment, and tool-use
digests. The server independently and atomically claims that exact authorized
assignment for the calling connection, then commits worker role only after the
terminal assignment read succeeds. A direct MCP client cannot mint the host
signature, and locator possession alone never establishes worker authority.
The candidate `tools/list` projection advertises a separate closed `read_task`
input contract containing only a worker-reference field, the sole assignment
view, and bounded continuation. General coordinator
state/evidence selector values and unknown fields
are absent from this candidate schema and are rejected without mutation.
The server-rendered spawn message remains the sole delivery of the opaque
worker locator. The child's first assignment read supplies the authoritative
full policy, profile, task contract, and predecessor evidence.

### Cortex 1.14.14 bundled skills and advisory roles

The bundled `orchestrator` and `cortex-control` skills are the authoritative
runtime model contract. A delegation carries bounded assignment data and exact
worker requirements; advisory profile templates supply role-specific workflow
and quality guidance without choosing a model, pinning an effort, authorizing a
tool, or imposing a lifecycle.

Codex normally supplies an activated bundled skill through host skill context.
Live workloads must use the real `$cortex:orchestrator` token or a host
skill-picker selection, never decorative bracket text. After compaction, the
supported `SessionStart(source=compact)` host hook reinjects the complete exact
packaged skill with `additionalContextLimit=0`; `PostCompact` remains
observation-only, and the standard host skill loader may repeat that
load whenever needed. Repetition is not prohibited or consumed. The reload
uses no `cat`, shell/filesystem inspection, approval, elevated execution,
`read_mcp_resource`, `resources/read`, Cortex tool, project copy, or `skill://`
URI. The active audience-specific semantic
registry remains the sole tool-shape authority.

The model records report evidence by compact ref. It should preserve material
contradictions, assumptions, decisions, verification outcomes, and unresolved
risks across delegations. Worker reads create immutable consumption receipts;
there is no universal phase order or profile-capability admission. The active
plan/approval and closure relations instead require their exact documented
evidence lineage.

Project-facing evidence is always worker-produced. The coordinator may compare,
challenge, and synthesize reports, but a missing or contradictory claim triggers
another worker delegation—not a coordinator file read, search, command, or
test.

Project-facing evidence includes root resolution and all project-local
artifact/state checks: Git, manifests, caches, worktrees, existence/absence or
unchanged-state, and `.codex`. A user request asking the coordinator to check
one of these is translated into a worker delegation.

A canonical project root is not evidence that the project is a Git worktree.
Before invoking Git, a worker uses a bounded capability probe that normalizes
unsupported/non-Git state to a clean successful observation. Git-dependent
inspection is skipped for a non-Git project; speculative Git failures are not
used as discovery evidence.

Documentation follows the same evidence boundary. Once project verification is
reported, the coordinator either delegates a material documentation-sync change
and an independent verification of it, or obtains a finalized worker-owned
documentation-impact report with an explicit material/no-impact rationale. The
stage is mandatory as a decision but conditional as an edit, and it precedes
advisory closure and the final answer. Missing update or
documentation-verification evidence leads to model-owned rework, replacement,
or explicit risk disclosure; it never becomes a backend lifecycle gate.

### Cortex 1.14.14 governance, security, and verification

Governance assessments are append-only. An explicit user override is stored as
`source=user_override` and is never rewritten by a backend classifier. The
latest user override remains the effective projection across later model
assessments; those revisions name the new evidence and can warn or recommend a
different depth without silently replacing the user's choice.

Project initiatives use `proposed`, `active`, `paused`, `completed`, `closed`,
or `cancelled`. The backend accepts any transition among these values. Parent,
dependency, task, and report relationships stay project-scoped. Missing or
cyclic same-project dependencies are retained as warnings and do not block
status updates or closure.

Task and initiative advisory closure verdicts remain `ready`, `ready_with_risks`,
and `not_ready`, but the public closure review is a distinct user decision.
After presenting the result, the coordinator shows exactly two localized
choices: revise the same task, or close the task. Ordinary clarification is
not this review: `open_clarification`/`record_clarification` records a direct
answer to a product or requirement question, while closure review consumes the
current result and asks only for revise-versus-close. A revise decision keeps
the same `task_ref`; a later assignment, report, or decision makes any prior
closure approval stale. The public `close_task` path atomically requires the
current consumed close choice, so an old, missing, or reused choice cannot
close the task. The internal advisory store may remain policy-neutral; these
public close/revise invariants are enforced at the task-facing boundary.

`read_task` exposes exact independent projections. `execution_outcome`
contains `evidence_status`, `finalized_report_count`, `completed_report_count`,
`effective_revision`, `coverage_status`, and `outcome`. The finalized count
covers every finalized report, while the outcome is derived deterministically
from current effective-contract coverage. `completed` requires `ready`; the
other coverage statuses produce `incomplete`. This projection does not claim
native lifecycle.
`advisory_closure`
contains `record_status` (`recorded` or `not_recorded`) and `latest_record`,
which is either the latest closure object or `null`. A closure record never
rewrites `execution_outcome`.

`close_task` first attempts the advisory write and then inspects
the intended task or initiative record. `closure_confirmation` reports
`inspection_status` (`confirmed` or `unconfirmed`), a reason
(`record_inspected`, `persistence_unavailable`, `inspection_unavailable`, or
`record_not_observed`), and `attempts` (1 or 2). Only one same-idempotency retry
is made for a verified transient persistence or inspection failure. If the
record cannot be confirmed, the coordinator discloses the unconfirmed
bookkeeping while preserving and reporting the independent user-work outcome;
completed work is not described as open or undone.

### Why this is more reliable than ordinary multi-agent work

- **State survives compaction and resume.** With the retained `task_ref` for
  calls and canonical `task_id` for durable evidence, tasks,
  delegations, reports, assessments, initiatives, and closures remain in the
  project ledger and can be read in bounded pages through `read_task`; the
  server does not scan for a lost task or recover a
  native worker. The coordinator restores user language, current plan, pending
  user decision, and human-view status from canonical ledger evidence, never by
  parsing Markdown; any exact pre-compaction state is reread before a decision
  mutation. An already-bound worker may reread its immutable assignment from
  the beginning on the same authenticated connection, without new receipts or
  authority, and rebuild terminal publication coverage from the fresh
  reconciliation projection. Fresh or copied connections remain rejected.
  The activation guard enforces the recovery order: coordinator mutations wait for the fresh state read,
  and worker publications wait for the terminal assignment reread. Recovery
  reads and later mutations are separate direct calls. More generally, the
  guard rejects every Cortex invocation hidden in programmatic `exec`, for both
  coordinator and worker routes, because nested calls hide their individual
  contracts and results from the model and host hooks.
  Steering additionally requires and consumes a same-connection state read
  performed after its successful opening.
- **Retries are idempotent.** Same-payload replays return the original record;
  conflicting replays cannot mutate the ledger.
- **Evidence is explicit.** Later workers receive stable immutable report refs,
  and reads preserve the coordinator's requested order.
- **Governance improves judgment without inventing a workflow engine.** Risks,
  mode history, warnings, plan-review evidence, and closure recommendations
  inform reasoning. Exact references remain strict when supplied, but no
  governance relation admits work or selects the next worker or solution.
- **The coordinator can adapt without becoming a worker.** It can revise mode,
  create rework, delegate independent verification, replace a worker, ask the
  user, or finish with disclosed risk without a server-owned recovery ceremony
  or direct project access.
- **Project programs are durable.** Initiatives link several tasks and reports
  and can outlive one orchestration task.
- **Documentation does not override source.** Project and feature pages provide
  navigation; delegated workers verify consequential claims against source,
  schemas, executable configuration, and tests.
- **Privacy is the default.** State remains local, and raw reports, secrets,
  personal data, and diagnostics are excluded from logs and documentation.

### Internal structure

```text
plugins/cortex/
├── .codex-plugin/plugin.json   # Manifest and UI metadata
├── .mcp.json                   # Local fourteen-tool semantic MCP server
├── agents/                     # 22 advisory role templates
├── assets/logo.png             # Plugin logo
├── profiles.json               # Advisory profiles and model recommendations
├── scripts/                    # Direct MCP server and schema-v1 runtime
└── skills/                     # Authoritative bundled runtime model contract
```

The Cortex 1.14.14 database is separate for each resolved project root:

```text
~/.codex/cortex/v12/projects/p-<sha256-of-resolved-project-root>/cortex.db
```

Schema v1 contains tasks, delegations, report manifests and chunks, plan
metadata, append-only user decisions, governance assessments, initiatives,
append-only initiative revisions, current initiative links, governance
closures, an ordered timeline, human-view projection jobs, idempotency records,
and additive migration metadata. SQLite transactions, WAL, uniqueness, and
foreign keys preserve concurrent integrity. State/project/task/view directories
are `0700`; database, WAL, SHM, and generated Markdown files are `0600`; and
symlink or non-regular database/view paths are rejected before use. The
host-private Markdown tree documented above is derived from these rows and is
never read back as authority.

The first normal `open_task` open automatically upgrades the one released
pre-human-view 1.12.1 layout transactionally, preserving existing rows and
canonicalizing its legacy reports into immutable chunks. Other unknown or
future layouts fail closed; V11 remains a separate, untouched database family.

Cortex 1.14.14 never opens, migrates, deletes, or modifies V11 databases. V11 tools and
unfinished V11 tasks are incompatible with Cortex 1.14.14; the historical V11 namespace
is neither an identity source nor a fallback recovery surface.

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
| `gpt-5.6-luna` | `high` | Default bounded work, including Explorer/discovery, ordinary implementation, QA, and deterministic rechecks; raise Luna effort before changing models |
| `gpt-5.6-terra` | `high` | Only evidence-backed genuinely complex non-security implementation, cross-cutting analysis, demanding review, or planning that benefits from Terra |
| `gpt-5.6-sol` | `high` | Security work and security-focused review only |

All three models support `low`, `medium`, `high`, `xhigh`, and `max`. Those
values are a native transport support boundary, not a backend policy matrix.
The model may choose a non-recommended supported effort when the assignment
warrants it.

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
genuinely complex non-security work or planning is evidenced; Sol remains
reserved for security-focused work and review.

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
| `plugins/cortex/scripts/cortex.py` | Cortex 1.14.14 MCP server facade |
| `plugins/cortex/.mcp.json` | Direct Python MCP server startup configuration |
| `plugins/cortex/scripts/cortex_runtime/v12_contract.py` | Bounded task/report constants and canonical report digests |
| `plugins/cortex/scripts/cortex_runtime/v12_store.py` | Project-isolated schema-v1 storage |
| `plugins/cortex/scripts/cortex_runtime/v12_projections.py` | Host-private derived Markdown materialization |
| `plugins/cortex/scripts/cortex_runtime/v12_maintenance.py` | Task-anchored host-private operator CLI outside MCP |
| `plugins/cortex/scripts/cortex_runtime/worker_message.py` | Attested direct native worker-message rendering |
| `plugins/cortex/scripts/cortex_runtime/public_contracts.py` | Exact uniform fourteen-tool semantic catalog |
| `plugins/cortex/profiles.json` | Advisory profiles and model recommendations |
| `plugins/cortex/skills/orchestrator/SKILL.md` | Single authoritative outcome-first orchestration skill |
| `plugins/cortex/skills/cortex-control/SKILL.md` | Authoritative fourteen-tool semantic, nonblocking, and task-anchor semantics |
| `.agents/plugins/marketplace.json` | Repository-local Marketplace |
| `scripts/sync-cortex.sh` | Synchronize and verify this local source checkout during development |
| `scripts/cortex-dev` | Start an interactive Codex session in the persistent isolated `$HOME/.cortex-dev` candidate runtime |
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
Codex. The candidate HOME, `CODEX_HOME`, plugin cache, configuration, and Cortex 1.14.14
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

Cortex 1.14.14 also packages a local administrator CLI for explicit health, project-shard
backup, checkpoint, optimize, vacuum, offline restore, derived-projection
prune/regeneration, and sealed-backup retention. It is **not** an MCP tool and
does not change the complete fourteen-tool semantic registry or either
audience projection. Every operation starts from an
existing Cortex 1.14.14 `task_id`, derives the host-private shard from that ID, accepts no
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

The current Cortex public contract release is **1.14.14**. Version and build identity are
defined by `plugins/cortex/.codex-plugin/plugin.json`. The installable manifest always
uses `1.14.14+codex.sha256.<digest-prefix>` in both the GitHub Marketplace package and
the isolated development candidate; the MCP server continues to advertise semantic
version `1.14.14`.

When changing the plugin, update the version according to SemVer:

- patch for a fix without new functionality;
- minor for a backward-compatible feature;
- major for a large or breaking change.

Build metadata after `+` is content-addressed as
`codex.sha256.<digest-prefix>`; it identifies the exact isolated candidate and
the exact production package and cannot be reused for different bytes. Runtime
startup recomputes the packaged digest before MCP initialization and rejects a
missing, stale, or invented suffix outside explicit source mode. An explicitly
source-mode checkout may use plain `1.14.14` or retain its last stamped suffix
while edited and reports `parityVerified=false`; neither is an installable
release until release validation stamps the exact current digest. The
product/server compatibility boundary remains `1.14.14`. V11 tools and unfinished
V11 tasks are not compatible with Cortex 1.14.14.

### Development agreements

- Do not create a second repository-level copy of the orchestration skill.
- Preserve exact machine-readable profile names from `profiles.json`.
- Keep profiles advisory; never add model/effort pins or tool authority to role
  templates.
- Keep the complete registry at exactly fourteen canonical tools, with a
  pre-identity neutral complete projection, an immutable coordinator
  projection, and a four-tool worker projection. Catalogue visibility never
  substitutes for authoritative per-call role enforcement.
- Keep the root coordinator orchestration-only. All project discovery, source
  inspection, domain analysis, edits, commands, builds, tests, and verification
  belong to delegated workers. Its only project-read exception is the bounded
  orchestrator-owned knowledge route used to compile delegation requirements.
- Keep that route closed to non-shell direct reads of already-known exact paths.
  Delegate unknown roots/paths, shell/search/graph discovery, and every
  project-local state or artifact check, including `.codex` absence or
  unchanged-state.
- Keep the exact path list and six-part knowledge-contract template only in the
  bundled orchestrator skill. Profiles consume the compiled per-delegation
  contract and never independently redo documentation routing.
- Pass the exact resolved `project_root` only to `open_task`. Use the exact
  server-issued task anchor on task-scoped calls; do not copy a long
  UI-rendered `task_id` or infer a root from MCP metadata,
  process `cwd`, thread identity, database scanning, or a lifecycle hook.
- Treat the returned public `task_ref` and server-rendered evidence as opaque
  byte-for-byte data. Private IDs, digests, and continuation state are also
  non-callable; never parse, reconstruct, normalize, concatenate, or suffix any
  of these values.
- Keep delegation `scope` a required non-empty text boundary; put execution
  detail in `instructions`, and reject object-shaped scopes.
- Select `profile_name` from the exact packaged names and verify loaded renderer
  proof; keep `role` as a separate human-readable label. Never treat free-form
  role text as profile proof. Limit unavailable fallback to degraded non-durable
  dispatch, disclose it, and supply a complete explicit role contract.
- Keep every native worker commentary, update, message, final response,
  tool-authored durable string, and worker-authored report narrative English;
  scan complete child threads, not only their final messages or database rows.
  Canonical product-facing reports and handoff payloads use one optional
  unchanged `source_text` value when they carry user-authored source material;
  they do not require language tags or translated/original pairs. Existing
  task contracts retain their designated original/language fields; decisions
  retain exact `response_original` without English duplicate fields. Localize
  coordinator-to-user summaries and links.
- Keep publication evidence behind the three current worker-owned publication
  operations. Do not expose unbounded report bodies through task or assignment
  inspection. The owning native worker alone publishes its evidence; the
  coordinator waits and consumes the worker's concise handoff plus the public
  `task_ref/state/replayed` receipt; any publication identity remains private.
- Treat the three narrow decision record operations as append-only coordinator-attributed evidence.
  Bind plan/report decisions to exact canonical digests. Preserve the one
  documented light/full plan-approval relation; never add approval, rejection,
  cancellation, or missing-review admission rules beyond it.
- Keep human-readable Markdown host-private beside the canonical database.
  Never write Cortex artifacts under `project_root`, never parse views back into
  SQLite, and publish only verified current absolute paths with localized
  summaries.
- Keep operator maintenance outside the MCP registry. Derive its only targets
  from a retained Cortex 1.14.14 `task_id`; accept no project root, arbitrary path, or V11
  target; preserve canonical data during projection/backup cleanup.
- Keep restore strictly offline. `RESTORE`, exact task/shard, backup ID, and
  `MCP_STOPPED` record deliberate operator intent but never substitute for
  actually stopping every normal MCP process using the shard.
- Never add initiative status, dependency warnings, or closure verdict to
  core-operation admission checks. Preserve only the initial governance-
  assessment requirement and the exact current-plan approval check for
  light/full delivery; report lineage otherwise remains evidence, not scheduling.
- After worker-reported project verification, make the report-grounded
  documentation-impact decision before closure. Material changes require a
  documentation-sync worker plus a separate verifier for `docs/project/` and
  `docs/features/`; no impact requires a finalized worker-owned report with an
  explicit English documentation-impact section and material/no-impact
  rationale, linked in the final initiative and cited by exact report ref and
  digest in closure evidence. A coordinator
  `documentation_not_required` assertion alone is invalid.
- Use `apply_patch` for source edits and prefer read-only validation modes before
  installation.
- Never commit `.codex/cortex`, runtime ledgers, diagnostic logs, credentials,
  raw reports, personal data, or other private state.

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
starts an isolated MCP server, asserts the complete fourteen-tool semantic
registry and both audience projections,
and exercises schema-v1 storage, idempotency, concurrent writes, compact
inspection, bounded publication evidence, digest-bound plan decisions,
host-private projection security and tamper handling, zero project-root writes,
cross-project isolation, governance revision history, initiative status
transitions, unresolved/cyclic dependency warnings, `not_ready` rework, V11
preservation, activation/lifecycle hook contracts, and exact model/effort
transport.

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

The E2E acceptance case is multi-turn and runs in a separate test project. The LLM observes the pane, answers exactly one product clarification with the predefined safe answer, later approves the visibly rendered plan, and follows planner → implementation → independent verification → documentation-impact assessment → closure. It inspects every native worker event stream and fails on any hidden tool error or unexplained replay. The tmux transport never answers or approves autonomously.

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

Exercise several explicitly activated tasks: a bounded minimal task, light work
revised after security evidence, a `not_ready` closure followed by rework, and a
project initiative closed with a disclosed unresolved dependency. Include a
Russian-user plan-review scenario that publishes the verified immutable plan
revision and current-plan links with Russian summaries, records a digest-bound
decision, and keeps every worker commentary, message, final response, and
durable operational artifact in English. Confirm that the coordinator uses
only user interaction, Cortex ledger operations, native
worker coordination, worker reports, and the bounded orchestrator-owned
knowledge route. Every source/code/config read or search and every project edit,
command, test, and verification action must appear in a worker's delegation and
report. Confirm that coordinator documentation reads are limited to applicable
`AGENTS.md`, the two indexes, and task-relevant linked pages, use non-shell
direct reads of already-known exact paths, and never use shell, `rg`, `find`,
globs, graph/source/repository search, or candidate probes. Include a
required-plan case with no coordinator project operation before the review hold
and a reportless/rework case where a requested project-local `.codex` check is
delegated. Exercise a material
documentation-impact branch with documentation-sync and verifier workers, plus
a no-impact branch with a finalized worker-owned explicit English
documentation-impact section and material/no-impact rationale, linked by exact
report ref in the initiative and closure evidence, and no edit. Confirm that the
coordinator never self-asserts `documentation_not_required` without that report
and can still answer when closure or a current human view is missing, discloses the
view state and summarizes canonical evidence instead of publishing a stale
path, and requires no hook or server recovery sequence. Verify that Cortex
created no file or directory under the target `project_root` and that published
host-private task, report, plan, decision, and timeline paths are regular files
with current digest/sequence evidence.

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
