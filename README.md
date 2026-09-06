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
        It preserves tasks, advisory governance, one Markdown pipeline per task,
        and reports in local storage. The coordinator owns delegation,
        evidence assessment, user steering, and completion.
      </p>
      <p>
        <img src="https://img.shields.io/badge/Cortex-1.15.7-7c3aed" alt="Cortex 1.15.7" />
        <img src="https://img.shields.io/badge/Python-3.11%2B-3776ab" alt="Python 3.11+" />
        <img src="https://img.shields.io/badge/Codex-Desktop%20%7C%20CLI-111827" alt="Codex Desktop and CLI" />
        <img src="https://img.shields.io/badge/Storage-Markdown%20%2B%20SQLite-0f766e" alt="Markdown files and SQLite metadata" />
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
4. Preserve existing ~/.codex/config.toml and model preferences. Verify native
   subagent support; enable multi_agent_v2 only when needed by the installed host.
   Cortex does not require changing agents.default_subagent_model.
   Keep user approval review enabled; do not enable Ask for me / Approve for me.
5. Confirm the plugin catalogue includes the 22 `cortex:worker-*` specialist
   skills. Verify complete native skill loading or exact advertised SKILL.md reads;
   catalogue discovery alone is insufficient. Do not read TOML or server internals. See the current
   host compatibility limitation below. Start a fresh task after updating the plugin.
6. Confirm that the installed package exposes exactly the seven documented
   storage tools and includes the documented local lifecycle hooks. Run the relevant
   verification checks and start a new Codex task.

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
  - [Post-install verification](#required-post-install-verification)
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

Use a specific existing repository or worktree, for example `/workspace/my-service`.
The host supplies its absolute canonical directory. Each task stores that exact
project boundary; do not use a broad system or home directory as the project.

Task documents and the private SQLite metadata index are both project-local:
`.codex/cortex/<task>/` and `.codex/cortex/cortex.sqlite3`. The active Codex
home's `state_5.sqlite` locates native source records and validates the thread's
canonical project; it is not the Cortex store. Task/report association is checked on every
read. Keep the project's SQLite file and task directories together for offline
backups. The installed MCP configuration invokes `python3` directly, so its launch
environment must resolve Python 3.11 or newer.

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

The packaged MCP server uses the host-resolved `python3 -B` command. Cortex does
not hard-code `/usr/bin/python3`; `-B` prevents runtime bytecode from changing the
content-addressed package. If CLI and Desktop behave differently, compare their
Python versions and launch environments. Bundled lifecycle hooks use the host’s ordinary review and trust flow.

### Required Codex configuration

> [!IMPORTANT]
> Configure Codex before the first Cortex 1.15.7 orchestration, then start a **new task**.
> Cortex requires available native subagents. It does not require Luna or a
> change to the user's global default subagent model.

On hosts where it is not already enabled, the current V2 route uses:

```toml
[features]
multi_agent_v2 = true
```

Use only settings supported by the installed Codex version. Preserve unrelated
configuration. An omitted worker model override inherits the actual host model;
intentional overrides use exact available model identities and supported effort.
Do not silently switch native interfaces or register personal agent profiles.

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

The MCP setting affects Cortex storage tools only. It does not authorize shell
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

### Marketplace specialist delivery

All 22 specialist profiles are distributed as `cortex:worker-*` skills through the
standard plugin manifest. Each native worker loads its complete selected skill
before project work: use an attached body or read the exact SKILL.md path supplied
in the host's available-skills catalogue. Needed declared Markdown references load
on demand. An already attached live tool schema can be used directly; Cortex does
not require a separate catalogue bootstrap, a particular first-call order, or fixed
batching. Generated worker skills use exact generated bytes and a final completion
marker, so a successful partial range cannot be mistaken for a complete file. This is normal Codex
progressive skill loading, including in the plugin
cache; it does not authorize reading agent TOML or server internals.

Marketplace installation needs no personal agent registration, setup hook, custom
loader or profile selector. After an update, start a fresh task for the current
catalogue. Missing automatic skill injection in an inter-agent message does not
prevent loading the advertised skill file. See [host compatibility](docs/project/host-compatibility.md)
and the [completed 12-trial decision](docs/project/quality-evaluation.md#shortened-series-decision-2026-09-06).

Generated TOML exports remain available for explicit personal custom-agent use,
but Cortex orchestration always uses the packaged worker skills. The optional
`scripts/cortex_setup.py --install` manages only those personal exports and refuses
conflicting user files. It is not part of marketplace or dev preparation.


### Required post-install verification

Start a new task after installation and confirm that Cortex advertises exactly
`create_task`, `set_governance`, `create_draft`, `read_draft`, `write_report`, `list_reports`,
and `read_report`. All seven operations are available to the coordinator and native workers. The
package includes local lifecycle hooks for source memory, observation and exact file-integrity checks; they do not approve actions or decide task completion.

Confirm the required native-agent configuration and Python runtime. A small
explicitly selected task should create one pipeline, delegate bounded work,
read selected reports and save its result. Actual CLI and Desktop observations
are distinct from source validation; see [release evidence](docs/release-readiness.md).

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
9. Review the requested permissions and bundled seven-operation MCP server.
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
5. Confirm the seven storage operations and the bundled lifecycle hooks, using normal host trust.
6. Recheck native subagent availability and the host's actual model/effort support.
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
4. Confirm the seven storage operations and the bundled lifecycle hooks, using normal host trust.
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
| `$cortex:orchestrator <task>` | Start ordinary Cortex 1.15.7 coordination | `$cortex:orchestrator Find the race condition and fix it with tests` |
| `$cortex:orchestrator help` | Show read-only help without changing the project or task storage | `$cortex:orchestrator help` |
| `$cortex:orchestrator harvest` | Update missing or stale source-backed project knowledge | `$cortex:orchestrator harvest` |
| `$cortex:orchestrator harvest-refresh` | Re-audit and rebuild project knowledge documentation | `$cortex:orchestrator harvest-refresh` |
| `$cortex:orchestrator clear 7 days` | Delete this project's tasks and artifacts older than seven days, protecting active tasks | `$cortex:orchestrator clear 7 days` |
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
> source-backed knowledge baseline. Cortex 1.15.7 never blocks ordinary coordination
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

`harvest-refresh` rebuilds inventory from current source, audits every in-scope
page, independently checks completeness and performs a second no-change planning
comparison. Harvest preserves manual material outside generated blocks. The
coordinator reads necessary user sources and bounded evidence pages; workers own
index-driven routing, source discovery, documentation edits and checks.
Workers receive mandatory requirements directly and select only useful reports.
Missing indexes do not force harvest during an ordinary task.

#### Documentation impact is assessed after verified tasks

The coordinator obtains a concise documentation-impact finding from a specialist.
When updates are required, a worker loads the bundled documentation-sync skill,
updates affected knowledge and verifies it before the task is completed.
Independent checking is proportional to the material impact. A supported no-impact
conclusion may use existing evidence; no extra worker or fixed report section is
required merely to state it. Source, tests and executable configuration outrank prose.

Never put secrets, personal data, private reports or diagnostic logs into public
documentation.

---

Completed assignments can retain their context for explicit bounded follow-ups.
Each follow-up publishes a new immutable report. A handoff must identify that latest
report; useful links to the same worker's earlier reports are allowed. Coordinators
record acceptance in the pipeline and answer the user directly; a worker-authored
synthesis artifact is optional, not an extra completion requirement.

Workers send progress, questions, blockers and verification updates only through
the host's native parent/subagent channel. They never use
`codex_app.send_message_to_thread` or other app task-messaging tools, including for
messages addressed to their coordinator. Completed work returns through the automatic
native final handoff; no app-message approval is needed for worker updates.

## Preferred worker route: Codebase Memory MCP

A known filename or symbol does not establish its implementation: workers resolve
unknown code through Codebase Memory first, including in small repositories. Retained
current source and purely non-code text edits do not need redundant graph discovery.
The shared protocol names `codebase_memory` explicitly. Workers discover its needed
operations separately from Cortex report tools; a Cortex-only catalogue lookup does
not establish whether the graph tools are available.
Every delegation starts with the exact worker-skill token and requires complete
skill loading before tool discovery or project work, so these rules reach the worker.

Workers use available graph tools before filesystem searches for definitions,
callers, dependencies and impact. They match `list_projects` to the exact workspace,
resolve symbols with `search_graph`, trace relationships with `trace_path`, and read
selected implementations with `get_code_snippet`. Scoped `get_architecture` and
schema-grounded `query_graph` serve broader questions; literal/configuration/docs
searches can use `search_code` or ordinary text search directly.

Workers check relevant index coverage, handle pagination and confirm consequential
facts in current source. Duplicate project names, stale/partial indexes and empty
results are not proof that code is absent. When several indexes share the same root,
workers compare health and relevant coverage: a ready index may exclude the assigned
subsystem.
Missing indexes may be built for the authorized workspace; watched indexes are not
rebuilt for every task. A missing or
insufficient MCP produces a concrete limitation and scoped source fallback. See
[knowledge routing](docs/features/knowledge-routing/index.md).

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
> coordinator delegates both structural discovery and documentation-index routing
> to workers, keeping the main context focused on pipeline state and report previews.

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
Cortex storage capability. Its absence allows one bounded safe fallback; only
inability to establish the assigned surface after that fallback is a worker
blocker. Reports can preserve that limitation and support an honest final answer.

---

## How orchestration works

Select `$cortex:orchestrator` explicitly. Cortex then retains a task through ordinary
follow-ups; `normal` leaves coordination and source capture without deleting its
archive. Help is read-only. Naming the repository or asking a complex question
never activates Cortex by itself.

The coordinator preserves the user's selected model, interprets requirements,
answers short questions, reads necessary user sources and evidence, delegates the
main technical work, and decides acceptance. A worker can investigate, implement,
verify and document one bounded result. Additional specialists are useful for
specific expertise, independent evidence or parallel work, not mandatory stages.
Shared files, browsers, devices, ports and applications have one active owner.
A timeout does not release that ownership.

### Markdown pipeline and readiness

A task has one real `pipeline.md`, with its newest complete edition first. It
contains active requirements, cancelled conditions, decisions, assignments,
resource owners, unfinished actions and evidence references. Original messages
remain separate immutable reports. Source revisions and checked artifact versions
make the basis of a report visible. A new message or observed file change signals
possible stale evidence; the coordinator decides whether more verification is needed.
Bounded independent discovery can precede the first edition. The coordinator records
useful durable state before making dependency, shared-resource or acceptance decisions;
pipeline publication is not a fixed stage before every delegation.

The coordinator and workers can read the source and report pages needed to recover
requirements. A page is limited to 4,000 Unicode characters; the available context
is not. Catalogue entries and opening decision briefs support selective retrieval.
An unavailable attachment remains a visible gap with its recovery reference when
one is known. An unavailable host journal does not close existing report access;
receipts describe source-capture completeness.

### Native workers and results

Assignments name a specialist skill, expected result, mandatory constraints,
ownership, checks and necessary references. They do not paste a long startup
protocol. Workers load their complete advertised skill normally and use applicable
skills for documents, spreadsheets, research, designs or applications. Verification
must establish the user's requested result, not automatically just passing code tests.

Workers reason and communicate only in English from their first response, including
progress updates, skill-loading commentary and context recovery. Assignments carry
this requirement before the worker loads its skill. Workers publish immutable
English reports and return their handoff once through the native final response.
The coordinator uses the user's own language for updates,
questions and final answers, unless the user explicitly requests another response
language. English evidence, forwarded agent messages and recovery summaries do not
change that choice; requested product language remains independent. The coordinator
owns pipeline editions. Reuse a completed suitable context for
a bounded continuation; use a fresh context when independent evidence is warranted.
A confirmed terminal failure permits recovery from saved reports, unfinished drafts
and actual project state. Missing results are not completion.

### Storage, receipts and Markdown files

The seven MCP operations remain `create_task`, `set_governance`, `create_draft`,
`read_draft`, `write_report`, `list_reports` and `read_report`. Their advertised
schemas contain argument, provenance, binding, change-query and retry contracts.
Host thread metadata resolves the task; the model never guesses task identifiers.
Each canonical project has its own SQLite store at
`.codex/cortex/cortex.sqlite3`; projects never share a Cortex database. SQLite
holds metadata and relationships, while report bodies live under project
`.codex/cortex/<task>/`. Editable drafts live under `.cortex/` because Codex protects
ordinary writes to project `.codex/`. Requests for one project serialize through
that project's store while unrelated projects use independent stores.

The server streams full UTF-8 drafts, verifies original file identity, atomically
publishes files and commits metadata. Exact retries return the accepted receipt.
New drafts default to server-generated delivery identities, so a worker's later
assignment does not depend on remembering earlier report keys. An uncertain
unkeyed creation is recovered through the caller's unfinished-draft catalogue;
repeating it creates another draft. Explicit keys retain exact-retry protection.
Pipeline recovery and retention stay inside the affected task, so a corrupted
neighbour does not block another archive. Validated file identities and page offsets
avoid repeated full reads of unchanged reports; changed files are revalidated.

Storage format 11 requires the separate offline 10→11 migration and a backup with
access stopped. Migration changes metadata only and leaves Markdown bytes intact.
A legacy shared v11 store can be split into a fresh project-local store with
`plugins/cortex/scripts/cortex_split.py`; the split also requires stopped access,
a new backup, and leaves the source and Markdown unchanged. See
[storage and migration](docs/project/storage.md).

### Lifecycle hooks

The bundled hooks call one short `python3` handler and share storage/validation
services with MCP. Active tasks capture follow-up source messages and compact
receipts; inactive conversations are not archived. Resume and compaction receive a
bounded recovery reminder through `SessionStart`, with repeated unchanged reminders
suppressed. Subagent binding uses explicit native receipts rather than treating a
parent `session_id` as the child identity.

`UserPromptSubmit` can only mark a pending follow-up because that event has no unique
native message identifier; publication waits for an authoritative typed receipt.
Stop observations are advisory, and the host may not expose the boundary of a reused
worker assignment.

On the observed CLI host, Bash hooks receive stdout without an exit code or running
session receipt. Cortex therefore keeps those hook outcomes unverified, even when
the text resembles JSON or a command wrapper. Native command receipts remain the
source of execution status.

Hooks record observations and diagnose unfinished work. They do not select models,
assign work, approve actions, accept results or force endless continuation. Patch
checks deny only confirmed integrity violations against registered Cortex files;
text merely mentioning a protected path is not a mutation. Hook coverage is partial,
and hook failures remain visible. See [lifecycle hooks](docs/features/lifecycle-hooks/index.md)
and the [official hook reference](https://learn.chatgpt.com/docs/hooks).

## Profiles and model routing

The coordinator reads as many bounded source or evidence pages as the decision
requires and uses previews to select relevant reports. Reuse a completed context
for an appropriate continuation; independent verification uses a fresh context when
required by the risk. See [comparative evaluation](docs/project/quality-evaluation.md).

Cortex includes 22 advisory specialist profiles:

| Area | Profiles |
| --- | --- |
| Discovery and planning | `explorer`, `planner`, `architect`, `database_architect` |
| Implementation | `frontend_dev`, `backend_dev`, `fullstack_dev`, `mobile_dev`, `data_engineer`, `devops_engineer`, `general` |
| Diagnosis and improvement | `debugger`, `refactorer`, `performance_engineer`, `ux_designer`, `accessibility_auditor`, `accessibility_fixer` |
| Quality control | `qa_engineer`, `code_reviewer`, `security_auditor`, `build_verification` |
| Documentation | `technical_writer` |

The host-supplied orchestrator skill contains a routing table for all profiles.
The coordinator names the exact packaged worker skill and supplies the
complete English assignment and constraints with `fork_turns: "none"`. The worker
loads its complete skill through host attachment or the exact advertised SKILL.md
path before applying it to project work. Already attached live schemas need no
catalogue round trip. Neither role explores the installation or reads profile TOML,
manifests or server internals. No custom loader is needed.

Profiles have structured role, input, workflow, quality, reporting and recovery
sections. One shared source protocol plus 22 specialization fragments generates
22 self-contained worker skills and matching optional Agent v2 TOML exports. A byte-for-byte test prevents
profile drift. Each profile also fixes its default draft class:

| Draft template | Profiles |
| --- | --- |
| `planning` | `planner`, `architect`, `database_architect`, `ux_designer` |
| `investigation` | `explorer`, `debugger` |
| `implementation` | `frontend_dev`, `backend_dev`, `fullstack_dev`, `mobile_dev`, `data_engineer`, `devops_engineer`, `refactorer`, `accessibility_fixer` |
| `verification` | `qa_engineer`, `code_reviewer`, `security_auditor`, `build_verification`, `accessibility_auditor`, `performance_engineer` |
| `documentation` | `technical_writer` |
| `general` | `general` |

Choose the report class that fits the assignment’s result. A profile default does
not require another worker for a suitable different class. Optional [report examples](plugins/cortex/skills/cortex-control/references/index.md)
for planning, investigation, implementation, verification, documentation and final
synthesis. Examples guide content without imposing exact report headings.

Codex supplies the selected orchestrator skill and the worker skill catalogue.
The host supplies required skills through Codex's normal procedure only when relevant.
Workers use exact advertised skill paths and declared references; they do not enumerate the installation.
[Tool discipline](plugins/cortex/skills/tool-discipline/SKILL.md) requires checking
live tool declarations, complete arguments and actual results; it forbids guessed
calls and unexplained mutation replays. Profiles do not authorize tools or select models.

### Adaptive model policy

Preserve the user's coordinator model and effort. Worker routing follows an explicit
policy: Luna (`gpt-5.6-luna`) is the default and priority model for ordinary work,
including all research, exploration and analysis assignments, at `medium`, `high`,
`xhigh` or `max`. Terra (`gpt-5.6-terra`) is reserved for work explicitly
classified as complex, with those same effort levels. Sol (`gpt-5.6-sol`) is
reserved for narrow security-analysis microtasks at `medium`, `high` or `xhigh`;
it is never selected for implementation merely because the task concerns security.
Security-related implementation uses Luna or Terra.

Reviews and verifications must be stronger than the implementation they inspect:
Luna implementations are reviewed with Terra; Terra implementations stay on Terra
with a strictly higher permitted effort where one exists (for example, `high` to
`xhigh`). Assignments record the implementation model and effort used for this
comparison. Every worker request states its model and effort explicitly. Other
models or efforts are forbidden for coordinator-selected work unless the user
directly requested that override; the exact request is recorded and preserved.

Obtain missing facts and tools directly; model escalation does not repair their
absence. Never change a worker merely for a slow response or one timeout. Compare
full task cost, including all participants, retries and cached input separately.
User requirements remain mandatory.

The coordinator owns model selection. The server and hooks do not silently select
agents or rewrite model requests; the isolated live observer audits actual worker
model/effort receipts and reports policy violations. The [new four-scenario
pilot](docs/project/quality-evaluation.md) compares protocol configurations;
unrun or unavailable measurements are explicit, never treated as zero.

---

## Developing Cortex

End users install through the Marketplace flow above. Repository development
prepares only the isolated candidate; it never updates the stable user plugin.

### Runtime boundary

The complete installable product lives under `plugins/cortex/`. Root-level
`scripts/`, `tests/`, `docs/` and `AGENTS.md` are development support.

| Path | Purpose |
| --- | --- |
| `plugins/cortex/scripts/cortex.py` | Seven-operation MCP entry point |
| `plugins/cortex/.mcp.json` | Direct Python server startup |
| `plugins/cortex/scripts/cortex_runtime/contracts.py` | Advertised schemas and limits |
| `plugins/cortex/scripts/cortex_runtime/host_source.py` | Read the current host thread’s typed user input within its project boundary |
| `plugins/cortex/scripts/cortex_runtime/store.py` | SQLite metadata and real Markdown storage |
| `plugins/cortex/scripts/cortex_runtime/project_storage.py` | Native project binding and project-local store routing |
| `plugins/cortex/scripts/cortex_runtime/server.py` | Bounded stdio transport and private errors |
| `plugins/cortex/scripts/cortex_clear.py` | Explicit host-side retention command |
| `plugins/cortex/scripts/cortex_split.py` | Stopped-access split from a legacy shared store |
| `plugins/cortex/profiles.json` | 22 advisory specialist descriptions |
| `plugins/cortex/skills/orchestrator/SKILL.md` | Coordination and model selection |
| `plugins/cortex/skills/cortex-control/SKILL.md` | Shared worker reporting protocol |
| `.agents/plugins/marketplace.json` | Repository Marketplace |
| `scripts/cortex_package.py` | Complete payload hashing and candidate validation |
| `scripts/sync-cortex.sh` | Read-only source validation or isolated candidate preparation |
| `scripts/cortex-dev` | Prepare candidate and launch ordinary interactive Codex |
| `scripts/cortex-desktop-dev` | Actual Desktop with a disposable profile |
| `cortex-dev` | Repository-root forwarding launcher |

### Isolated candidate runtime

`./scripts/cortex-dev` creates or reuses the exact `$HOME/.cortex-dev` candidate,
sets its own `HOME` and `CODEX_HOME`, prepares the content-stamped package through
the supported sync path and launches ordinary Codex in the caller's project.
It does not create tmux or modify the stable installed plugin.

```bash
./scripts/cortex-dev --prepare-only
./scripts/cortex-dev
# Equivalent interactive entry point:
./cortex-dev
```

The interactive CLI driver exposes the same complete observation gates:
`./scripts/cortex-live-smoke calls` and `./scripts/cortex-live-smoke audit`.
Run both before accepting or stopping a live session.

Actual Desktop uses the same prepared candidate and a disposable Electron profile:

```bash
./scripts/cortex-desktop-dev start --workdir /absolute/existing/test-project \
  --prompt-file /absolute/TASK_PROMPT.txt
./scripts/cortex-desktop-dev status
./scripts/cortex-desktop-dev send
./scripts/cortex-desktop-dev events
./scripts/cortex-desktop-dev calls
./scripts/cortex-desktop-dev audit
./scripts/cortex-desktop-dev stop
```

`CORTEX_DESKTOP_BINARY` can select the actual Desktop executable. `send` verifies
the isolated window PID, focuses its prepared composer, submits with `Ctrl+Enter`,
and records success only after exactly one new task receipt appears. It refuses a
second acknowledged submission while leaving a prompt retryable when no receipt
appears. `calls` emits the full run by default and correlates every coordinator and
worker wrapper, nested host invocation and actual Cortex MCP event while retaining
only argument/result digests and safe routing metadata. `audit` reads the same
complete history and fails on MCP or host errors, truncation, missing command
outcomes, forbidden role or file access, calls made by a worker after successful
publication without an explicit successful parent follow-up after final handoff,
oversized document pages, or a command session without a terminal result.
Every observed error is retained chronologically in `tool_error_history` and fails
the run even when a later retry succeeds. `resolved_host_failures` explains a later
correction but never makes that run acceptable. Pre-dispatch wrapper syntax errors
are separated from nested calls that never executed.
This exposes redundant or misplaced operations without commands or report bodies.
Inspect the visible result too. CLI and
Desktop qualification requires consecutive successful scenarios on one unchanged
payload. Any installable edit invalidates earlier live results.

### Recommended development loop

```bash
python3 -B scripts/cortex_package.py stamp
python3 -B scripts/validate-cortex-marketplace.py
./scripts/sync-cortex.sh --check
PYTHONDONTWRITEBYTECODE=1 python3 -B -m pytest -q
git diff --check
./scripts/cortex-live-smoke start --workdir /absolute/existing/test-project
```

Stamp before release-sensitive checks after every installable edit. Run package,
tests and sync checks sequentially in one checkout. Normal sync is allowed only
through `cortex-dev` in the exact isolated environment; `--check` and `--dry-run`
are read-only source checks. Observe the interactive composer and candidate receipt
before submitting an ordinary product workload.

### Operator maintenance

`$cortex:orchestrator clear 7 days` deletes only this project's tasks and all their
artifacts whose latest recorded activity is older than seven days. Active task
identifiers are protected using the coordinator's native-host knowledge. This
explicit instruction authorizes the bounded deletion without another confirmation.

The coordinator delegates the installed `scripts/cortex_clear.py` host command,
obtaining its usage from `--help`. It is not an MCP operation and does not
create a cleanup task. Committed deletion intents recover interrupted cleanup.
The operation does not remove project source or other projects' tasks. Back up
SQLite and task directories together while storage access is stopped.

### Versioning

This release uses semantic version **1.15.7** as requested. The manifest
and MCP server advertise `1.15.7+codex.sha256.<digest-prefix>`, computed from the
complete installable payload. Regenerate the suffix whenever that payload changes.
Different bytes must not reuse a stamp. The package validator and candidate
preparation verify it; the server is not a workflow compatibility layer.

### Development agreements

- Keep one authoritative bundled orchestrator and exactly seven public operations.
- Keep all 22 profiles with a shared free-form Markdown reporting workflow.
- Keep model selection, delegation, steering and completion in the coordinator.
- Store one latest-first pipeline file; ordinary reports remain immutable.
- Keep tool argument contracts in advertised schemas, not model instructions.
- Preserve help, harvest, refresh, clear, index routing and context rereading.
- Verify storage integrity without interpreting Markdown completion claims.
- Never retain compatibility routes, mandatory stages or approval machines; lifecycle hooks remain local storage and integrity helpers.
- Update affected documentation and record actual evidence and unrun checks.
- Refresh only the isolated candidate; never alter stable installation for tests.
- Never commit private task reports, credentials or diagnostic logs.

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

Run release-sensitive checks sequentially on one stamped checkout:

```bash
python3 -B scripts/cortex_package.py stamp
python3 -B scripts/validate-cortex-marketplace.py
./scripts/sync-cortex.sh --check
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q
git diff --check
```

Source tests cover storage, schemas, recovery, source capture, hook behavior,
profile generation and the observer. They cannot establish model behavior, host
hook coverage or task quality. Keep raw source messages, reports, logs and host
transcripts outside repository documentation.

### Interactive tmux live-dev workflow

Use only `./scripts/cortex-live-smoke start --workdir PATH` for the exact
`cortex-markdown-smoke` session on the default tmux server. Inspect `capture` and
`status`, visibly confirm trust before one explicit `enter`, and confirm the
composer. Compare the passive initialization receipt with the isolated candidate
and seven-tool catalogue. Workloads begin with `$cortex:orchestrator` followed by
ordinary product work. `send --prompt-file FILE` pastes once, waits five real
seconds and sends one named Enter; it requires a native input receipt.

Each new CLI smoke run uses the exact project-local store at
`PATH/.codex/cortex/cortex.sqlite3`. A `--resume-last` run reuses that same
canonical project store and rejects a missing or mismatched store. `stop` keeps the
project store for resume and removes only the session and observation streams.

Inspect complete `calls` and `events`, including after a discovered fault, and run
`audit` before stopping. A command wrapper must expose its exit status or running
session receipt. Capture `Cortex live-dev exit=0`, then use `stop` for that exact
session; use `stop --interrupt` after a failed run. Never kill the tmux server.
Resume uses the same workdir and `--resume-last`, with the existing task confirmed.

Real Desktop uses `scripts/cortex-desktop-dev start --workdir PATH --prompt-file FILE`
and the same isolated candidate in a disposable Electron profile. Confirm the
prepared composer, then `send` focuses that exact window, submits with Ctrl+Enter
and requires one new task receipt. Review its `calls`, `events` and `audit` too.
Use ordinary hook trust; never bypass it for qualification. CLI/Desktop parity
requires consecutive successful runs on one unchanged payload. Unavailable hosts
and unrun checks remain unverified.

See [verification](docs/project/verification.md),
[host compatibility](docs/project/host-compatibility.md),
[lifecycle hooks](docs/features/lifecycle-hooks/index.md),
[comparative evaluation](docs/project/quality-evaluation.md), and
[current release evidence](docs/release-readiness.md).

Live development uses Luna/high for the coordinator and Luna at medium/high for
native workers. The isolated helpers layer this user-requested test policy and audit
actual participant/selector receipts. Heavy live-test models are rejected. This does
not change stable settings or the plugin's general user-selected model policy.
