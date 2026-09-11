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
        <img src="https://img.shields.io/badge/Cortex-1.15.9-7c3aed" alt="Cortex 1.15.9" />
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
4. Preserve existing global $CODEX_HOME/config.toml (normally ~/.codex/config.toml)
   and model preferences while applying the required Cortex provider and
   compaction settings below. Verify native
   subagent support; enable multi_agent_v2 only when needed by the installed host.
   Cortex does not require changing agents.default_subagent_model.
   Keep user approval review enabled; do not enable Ask for me / Approve for me.
   Follow **Automatic provider and compaction paths** after both installation
   and update: start the installed Cortex MCP so it verifies the gateway and
   applies the global provider and compaction configuration automatically.
   Installing the plugin alone is not proof that this initialization ran.
   Inspect the effective global config and verify all of the following:
   - model_provider = "cortex";
   - model_providers.cortex points to the verified loopback listener, with
     wire_api = "responses", requires_openai_auth = true and supports_websockets = true;
   - features.context_management = false;
   - features.remote_compaction_v2 = true.
   Preserve unrelated settings and the generated backup/route journal. If a
   later user override prevents these settings from applying, explain the
   conflict and resolve that choice with the user instead of silently replacing it.
   Do not add proxy environment variables or a CA certificate. Verify a fresh
   model request through the gateway after Codex reloads configuration. If a new
   task or restart is still needed, report that explicitly and preserve active
   work. Do not claim compaction routing is verified without observing an actual
   compaction request and its routed model/effort.
5. Confirm the plugin catalogue includes the 23 `cortex:worker-*` specialist
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

Report storage uses the Python standard library. The enabled Model Gateway
automatically installs its hash-locked Python dependencies in a private Cortex
directory; Python must have `pip` available. The current gateway wheel set and
process verification target Linux CPython 3.11/3.12; macOS gateway qualification
is pending. Confirm that the required tools are available:

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
> Configure Codex before the first Cortex 1.15.9 orchestration, then start a **new task**.
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

All 23 specialist profiles are distributed as `cortex:worker-*` skills through the
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
| `$cortex:orchestrator <task>` | Start ordinary Cortex 1.15.9 coordination | `$cortex:orchestrator Find the race condition and fix it with tests` |
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
> source-backed knowledge baseline. Cortex 1.15.9 never blocks ordinary coordination
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

The coordinator tracks native workers spawned through `collaboration.spawn_agent`
only with `collaboration.wait_agent`, `collaboration.list_agents`,
`collaboration.send_message` and `collaboration.followup_task`. Codex app thread
tools such as `create_thread`, `read_thread`, `wait_threads` and
`send_message_to_thread` are reserved for explicit user-owned task management,
never for orchestration-worker tracking.

For an active Cortex task, the bundled `PreToolUse` hook denies both the canonical
`mcp__codex_app__send_message_to_thread` call and its direct alias before dispatch.
Tool-event payloads may not establish whether the caller is a worker, so this is a
task-wide denial that also applies to a coordinator. The native parent/subagent route
remains available for worker communication.

The isolated CLI/Desktop observer also recognizes direct, quoted-bracket and
simple static-alias worker calls to `send_message_to_thread`, including those in
known `functions.exec` wrappers, as forbidden orchestration outcomes and fails
its audit. This is bounded static inspection, not exhaustive JavaScript
evaluation. A qualification guard based on the host's observed call boundary
cannot remove an app connector that the host exposes. The installed Desktop
provider is injected dynamically as the `codex_app` MCP server; the candidate
`[mcp_servers.codex_app] disabled_tools=["send_message_to_thread"]` override
fails bootstrap with `invalid transport`, so the launcher does not claim a
per-tool filter or shadow the provider.

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

The live observer records Codebase Memory calls as ordinary worker tooling. A
successful or failed external provider call, Git/rg diagnostic, or other
non-Cortex command is not an orchestration violation by tool name or exit status.
Workers have a separate private-Cortex boundary: they never shell, search or
probe `.codex/cortex/` task-cache paths. Assignment-relevant immutable reports
are selected by exact acknowledged ID and read only through bounded
`mcp__cortex__read_report` pages (at most 4,000 characters, continuing only with
the returned cursor). Missing or insufficient evidence is reported as a gap; it
is never filled by scanning the private cache. The observer keeps this cache
hard stop fail-closed. For a literal `rg`/`grep` command, a quoted search
pattern or glob containing `.codex/cortex/` is a static mention, while a path
operand, direct reader, or shell-ambiguous form remains an unauthorized hard
stop; unquoted filename globs (`*`, `?`, `[]`) and brace expansion (`{}`) are
ambiguous, while quoted literals remain static. Path-policy findings retain only safe provenance and target classes; only
an explicitly classified unauthorized target,
Cortex/transport contract breach, or task-impacting failure can enter the
orchestration acceptance gate.

The trusted `PreToolUse` hook provides a deny-only execution guard for this
boundary. It rejects worker shell, file, and patch operands that resolve inside
the confirmed task-private root before dispatch, including relative paths,
`cd`/variable-expansion forms, and existing symlink routes. The guard emits only
a sanitized denial and does not open or log the target. It intentionally leaves
bounded `mcp__cortex__read_report` reads and ordinary workspace, Git, Codebase
Memory, and external MCP diagnostics available. This preflight is defense in
depth; the observer's fail-closed hard stop remains authoritative for any
observed unauthorized event.
When a tool event omits `agent_id`, worker scope is established only from a
matching child-thread/parent-session provenance receipt validated against the
durable binding. Running worker command receipts retain their effective working
directory and command-session ID, allowing a later `write_stdin` to apply the
same guard. Unproven session-scoped events remain compatible with coordinator and
system tooling rather than being globally denied.

---

## How orchestration works

### Optional context-selected guidance

When relevant, the coordinator may use three advisory cues: map each fresh
completion claim to relevant evidence and list unrun checks; separate facts from
hypotheses and name one discriminating check before a nontrivial repair; and, before
parallel dispatch, declare independence, mutation surface, shared resources,
dependencies, and expected output. These cues are optional and do not create
mandatory stages, approval gates, fixed report sections, or automatic acceptance.

At a work transition, it may also consider one bounded senior consultation only
when both a consequence trigger and an uncertainty trigger apply. Consequences
include material architecture/public-tool/storage/security/release/migration or
multi-owner decisions, hard-to-reverse external/scope/compatibility choices,
contradictory repair-to-acceptance evidence or broad claims, and replanning after
repeated failure or surprise. Uncertainty includes material alternatives,
fact/hypothesis conflict or an untested critical assumption, no discriminating
check/evidence boundary, or a fresh view likely to change the decision. Routine,
reversible/deterministic work, ordinary tests, already-decided detail,
phase/time/report existence, executor-evidence requests, unchanged packets,
consultation chains/sign-off/fixed counts and active incident recovery are
excluded. The advice is optional, may run concurrently when safe and never blocks
by rule or transfers planning, steering, evidence, acceptance or communication.
The compact packet and coordinator record contain only the bounded question,
context, selected report/artifact identities, alternatives, facts/hypotheses,
attempts, ownership, decision and next check; no copied bodies or private data.
Quality and overhead are evaluated against the unchanged baseline.

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

After a Cortex task is created, project artifacts belong to the worker for the
whole mutation and verification boundary. The coordinator must dispatch the
worker before reading, editing, hashing or checking a project target, even when
the request is one small file; `functions.exec`, `exec_command` and terminal
wrappers are not coordinator shortcuts. User-supplied sources and the exact
Cortex-issued pipeline draft remain coordinator-readable, and the coordinator
accepts the worker's receipts and report for project evidence.

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

The coordinator never emits a terminal final answer while an assigned owner is
active or a required report/check remains outstanding. A wait timeout means only
that no new evidence arrived, so the same owner receives another bounded native
wait. Before acceptance or a final answer, the coordinator reconciles pipeline
assignments with native worker state and required evidence; terminal failure or
user cancellation must be explicit and recorded. Interim updates must say clearly
that work remains active.

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
Only the bound coordinator can create a `pipeline` draft; worker pipeline requests
are rejected before allocation, while ordinary worker report drafts remain allowed.
Worker report creation still supplies the required `template` argument explicitly;
ordinary worker reports use `general`, and an empty draft-creation argument object
is invalid.
The coordinator treats the `create_draft` Markdown and its exact
`replaceable_markers` list as authoritative: every listed marker is replaced in
the same draft before `write_report`, and document kind matches ownership
(`pipeline` for the coordinator, non-pipeline report for a worker). A
`draft_guidance_remaining` rejection is corrected in place and retried once with
the same request key and metadata; it does not create a competing draft or replay
an acknowledged publication. This is workflow guidance, not an approval gate.
When a coordinator must provide `request_key`, it uses a literal valid UUID or
stable literal key in the tool arguments. Runtime expressions such as
`crypto.randomUUID()` are not used inside wrappers because the host may not expose
that JavaScript global before Cortex receives the call.
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
The recovery reminder repeats the project-access boundary. When a host explicitly
identifies the coordinator on a command/file event, `PreToolUse` denies that route
before dispatch; events without actor identity remain observable and are rejected
by the audit if they show coordinator project access.

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
checks and the pre-dispatch app-thread-message denial cover only their exact routes;
text merely mentioning a protected path is not a mutation. Hook coverage is partial,
and hook failures remain visible. See [lifecycle hooks](docs/features/lifecycle-hooks/index.md)
and the [official hook reference](https://learn.chatgpt.com/docs/hooks).

## Profiles and model routing

The coordinator reads as many bounded source or evidence pages as the decision
requires and uses previews to select relevant reports. Reuse a completed context
for an appropriate continuation; independent verification uses a fresh context when
required by the risk. See [comparative evaluation](docs/project/quality-evaluation.md).

Cortex includes 23 advisory specialist profiles:

| Area | Profiles |
| --- | --- |
| Discovery and planning | `explorer`, `planner`, `architect`, `database_architect` |
| Implementation | `frontend_dev`, `backend_dev`, `fullstack_dev`, `mobile_dev`, `data_engineer`, `devops_engineer`, `general` |
| Diagnosis and improvement | `debugger`, `refactorer`, `performance_engineer`, `ux_designer`, `accessibility_auditor`, `accessibility_fixer` |
| Quality control | `qa_engineer`, `code_reviewer`, `security_auditor`, `build_verification` |
| Consultation | `senior_consultant` |
| Documentation | `technical_writer` |

The host-supplied orchestrator skill contains a routing table for all profiles.
The coordinator names the exact packaged worker skill and supplies the
complete English assignment and constraints with `fork_turns: "none"`. The worker
loads its complete skill through host attachment or the exact advertised SKILL.md
path before applying it to project work. For `senior_consultant`, the coordinator
supplies the complete skill and publication reference as attached instructions or
verbatim bodies; the consultant does not run commands to load guidance.
The native assignment is named `senior_consultant` for observable role attribution.
Missing tool declarations are discovered individually; publication uses only the
live declaration's fields and a complete call whose receipt is inspected.
Already attached live schemas need no
catalogue round trip. Neither role explores the installation or reads profile TOML,
manifests or server internals. No custom loader is needed.

Profiles have structured role, input, workflow, quality, reporting and recovery
sections. The 22 executors share one source protocol; the consultant has a separate
report-only protocol. These protocols and 23 specialization fragments generate
23 self-contained worker skills and matching optional Agent v2 TOML exports. A byte-for-byte test prevents
profile drift. Each profile also fixes its default draft class:

| Draft template | Profiles |
| --- | --- |
| `planning` | `planner`, `architect`, `database_architect`, `ux_designer` |
| `investigation` | `explorer`, `debugger` |
| `implementation` | `frontend_dev`, `backend_dev`, `fullstack_dev`, `mobile_dev`, `data_engineer`, `devops_engineer`, `refactorer`, `accessibility_fixer` |
| `verification` | `qa_engineer`, `code_reviewer`, `security_auditor`, `build_verification`, `accessibility_auditor`, `performance_engineer` |
| `documentation` | `technical_writer` |
| `general` | `general` |
| `general` | `senior_consultant` |

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
Discovery first lists tool names, then opens one selected complete declaration;
a name-only result cannot supply an input contract. Calls include every required
field; declared defaults apply only to optional controls. After compaction, actors
reload the next needed declaration and preserve the full command result, including
exit status or a running handle, from the first recovery read onward.
New workers receive self-contained assignments and selected evidence without the
coordinator's conversation history. Report publication follows its own live key
requirements, which differ from draft creation. The required draft identity line
is registration metadata and must not be treated as an unfilled guidance marker.

### Adaptive model policy

Preserve the user's coordinator model and effort. Worker routing follows an explicit
policy: Luna (`gpt-5.6-luna`) is the default and priority model for ordinary work,
including all research, exploration and analysis assignments, at `medium`, `high`,
`xhigh` or `max`. Terra (`gpt-5.6-terra`) is reserved for work explicitly
classified as complex, at `medium`, `high` or `xhigh`. Sol (`gpt-5.6-sol`) is
reserved for narrow security-analysis microtasks at `medium`, `high` or `xhigh`;
it is never selected for implementation merely because the task concerns security.
Security-related implementation uses Luna or Terra.

The opt-in `senior_consultant` route answers one bounded coordinator question from
selected published reports. Standard consultation is Sol (`gpt-5.6-sol`) at
`medium`; narrow and harder questions use Sol `low` and `high` respectively.
`gpt-6-astra` at `low`, `medium` or `high` is reserved for a justified deeper
escalation after Sol evidence remains unresolved. The consultant cannot read
project sources or indexes, run commands/tests/network/environment checks, edit
project files, launch agents, accept tasks, or alter the coordinator plan. Its only
write is its own Markdown report. Each packet carries one question, goal/constraints,
requirements revision, exact report IDs and artifact versions, attempts/results, and
facts versus hypotheses; repeated consultation requires new evidence or a clarified
question. Insufficient evidence must produce an exact data request and recommended
executor profile. The coordinator records the consultation and decision in the
current pipeline; the recommendation never replaces executor verification or
acceptance.

The coordinator supplies the consultant's complete instructions alongside its
compact evidence packet, without inherited conversation. Its separate protocol
keeps the 22 executor profiles unchanged. See the [consultation cycle and access
limits](docs/features/senior-consultant/index.md).

Reviews and verifications use the permitted model and effort routes without
automatic escalation from the implementation they inspect. Assignments record the
implementation model and effort when relevant to the review. Every worker request
states its model and effort explicitly. Other
models or efforts are forbidden for coordinator-selected work unless the user
directly requested that override; the exact request is recorded and preserved.
The `review` label records work kind and does not select a model; absent an
explicit complexity or security classification, it follows the ordinary route.

The isolated Desktop live-test launcher makes this provenance concrete: every
`spawn_agent` call must include `model`, `reasoning_effort`, and `fork_turns`
explicitly (`gpt-5.6-luna` with `medium`/`high` and `none` for ordinary workers;
the selected consultant route records its own permitted values). The observer
retains strict matching whenever requested fields are visible. If both are
unavailable because the assignment carries the exact
`assignment_content_unavailable=true` marker, it can verify only a complete,
unique native child/path/parent-edge/Luna/effort/fork/skill join; it never fills
missing fields from host defaults. Missing, conflicting or duplicate evidence
remains unverified. Native `SubAgentActivity` evidence coalesces one `started`
and one `completed` lifecycle record for that same child/path; repeated phases
or a path resolving to different children remain ambiguous and fail closed.
Valid declared Markdown-reference reads do not count as a second complete
worker-skill receipt. The observer also recognizes a quoted `rg`/`grep` exclusion
marker in a simple discovery pipeline as a static mention; direct private paths,
unquoted globs, and opaque shell syntax remain unauthorized.
Exact Python `-c` and quoted-heredoc audit comparisons may likewise contain a
private marker only as a direct literal comparison operand; Python assignments,
file/process calls, comments, interpolation, malformed programs and private
working directories remain denied. A worker's native final names only its own
assignment publication; input and predecessor report IDs stay in the report body.
Desktop calls, MCP events, hooks, open resources, and usage are attributed only to
the submitted coordinator and its native descendants. Every present native
thread/parent identity must agree with that tree; mixed identities fail closed.
A second recent root, duplicate edge, self-parent, cycle, or malformed recent edge
is reported as an overlap/topology failure rather than merging or hiding sessions.
Usage accounting consumes that same validated inventory and returns an explicit
`invalid_topology` result instead of silently dropping a multiply-owned child.
The coordinator cache exception covers only the exact advertised orchestrator
`SKILL.md`; worker skills, other skills, and references remain forbidden to it.
The c4 follow-up parity experiment uses a fresh `mktemp -d` directory initialized
with `git init` for both hosts, avoiding historical task roots without turning the
clean-project setup into a general product stage.

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
| `plugins/cortex/profiles.json` | 23 advisory specialist descriptions |
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
The packaged MCP manifest forwards the isolated dependency directory so cached
MCP startup can launch the gateway with the same hash-locked dependency set.
The Desktop helper derives and validates that prepared owner-only directory
after `--prepare-only`, overwriting any conflicting ambient value before
launching Electron.

```bash
./scripts/cortex-dev --prepare-only
./scripts/cortex-dev
# Equivalent interactive entry point:
./cortex-dev
```

The interactive CLI driver exposes the same complete observation gates. Its
ordinary graph-disabled mode suppresses an inherited complete optional
`codebase_memory` entry in the disposable profile; it leaves absent or incomplete
entries untouched and never changes the stable profile:
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

When xdotool `windowactivate --sync` returns the exact
`XGetWindowProperty[_NET_WM_DESKTOP] failed (code=1)` warning, `send` records the
bounded warning and uses one `windowfocus --sync` fallback only after rechecking
the launched PID. It then requires `getactivewindow` to name that same window and
rechecks its PID before any click or `Ctrl+Enter`. Other activation errors,
ownership changes, failed focus, or an active-window mismatch fail closed; the
existing composer and exactly-one-receipt gates still decide delivery.

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

This release uses semantic version **1.15.9** per the current release instruction. The manifest
and MCP server advertise `1.15.9+codex.sha256.<digest-prefix>`, computed from the
complete installable payload. Regenerate the suffix whenever that payload changes.
Different bytes must not reuse a stamp. The package validator and candidate
preparation verify it; the server is not a workflow compatibility layer.

The stable release candidate is `1.15.9+codex.sha256.e4f332d43bf38024`.
Final consecutive real CLI and Desktop qualification passed on this unchanged
candidate (`r_648c4f40dcf9`), alongside the recorded offline tests and independent
reviews. The user cancelled the Phase 2 outcome comparison; it is non-blocking
and non-scoring, and Cortex makes no efficacy-comparison claim for this release.
Earlier failed or superseded candidates remain historical diagnostics only.

### Development agreements

- Keep one authoritative bundled orchestrator and exactly seven public operations.
- Keep all 23 profiles with a shared free-form Markdown reporting workflow.
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

The Model Gateway lifecycle and provider patch behavior are documented in
[Model Gateway lifecycle](docs/project/model-gateway.md). Marketplace MCP
startup enables the gateway by default through the global `CODEX_HOME`
configuration; set `gateway.enabled = false` for explicit storage-only mode.
The isolated launcher installs and
version-checks a clean dependency target from the hash-locked Linux wheel set
with pip `--require-hashes`, then invokes the prepared Marketplace cache's own
gateway control entrypoint; runtime health binds the lock manifest digest,
installed dependency-byte digest, exact stamped payload, and entrypoint
invocation. If the explicit gateway configuration is changed to
`gateway.enabled = false`, the launcher ensure path drains the owned child and
waits for runtime-state removal before reporting it stopped. A reload to the
disabled state stops the child and request handling rejects any race rather
than forwarding; if reload cannot signal or drain the owned child, it returns
failure and the control command exits nonzero. Draining health is not
readiness. After readiness, Cortex automatically selects its local Codex
provider at `http://127.0.0.1:8787/backend-api/codex` (or the configured fixed
loopback listener). It uses Codex's existing OpenAI authentication and preserves
model choices, reasoning effort and unrelated configuration. It selects the
remote summary compactor with `remote_compaction_v2 = true` and
`context_management = false`; both feature edits are journaled and reversible.
No proxy variables, local CA certificate or special product launcher are needed.
The proxy has no request-count or connection-slot admission limit: active-request
tracking exists only for controlled drain, and a full historical 32-request count
does not produce a capacity 503, `Retry-After`, or an admission queue. Body,
timeout, protocol, policy, and cancellation checks remain in force. Candidate
preparation uses the isolated listener (normally 18787) and never changes the
stable 8787 runtime.
Provider backups are
published create-only so concurrent configuration cannot overwrite the first
recovery point. Gateway configuration and its control directory are private
owner-only paths; unsafe existing entries fail closed. Provider updates use a
locked descriptor/content compare-and-swap and refuse to overwrite unrelated
concurrent edits.
When a requested listener is occupied by an older same-user Cortex gateway,
startup verifies its complete command and packaged manifest, sends a controlled
stop, waits for the listener to drain, and retries. An unrelated process is
never terminated. The packaged dependency installer keeps its progress output
off MCP stdout so it cannot corrupt the JSON-RPC handshake. Compaction
rewriting covers V2 `compaction_trigger` (auto compaction) and the
legacy `/backend-api/codex/responses/compact` JSON path (manual compaction).
Recognized legacy JSON is routed in-call to the supported V2 path because the
production upstream retired `/responses/compact`; opaque legacy bytes retain
their original path and bytes. The HTTP compaction routes force the upstream-
required `store: false` and `stream: true` values. The optional WebSocket
transport inspects only uncompressed direct `response.create` compaction
messages and rewrites only `model` and `reasoning.effort`, preserving the
message's other fields; it does not
apply a separate HTTP store rewrite.

### Automatic provider and compaction paths

After installation or update, the first startup of the installed Cortex MCP
applies these settings to the global `$CODEX_HOME/config.toml` (normally
`~/.codex/config.toml`), not a project-local config. Marketplace installation by
itself does not run this setup; MCP initialization must occur. Model traffic
uses the new settings when the client next loads configuration.

Automatic setup selects Codex's remote summary compaction mode. The experimental
`context_management` mode can reset a context window locally without asking a
model to summarize it; there is no summary request for a proxy to reroute in that
case. Cortex sets `features.context_management = false` and
`features.remote_compaction_v2 = true` on connection. These choices take effect
on the next configuration load, apply to native workers as well as coordinators,
and restore their previous values on disconnect if still unchanged. Subsequent
user feature overrides are preserved; opting back into context management also
opts out of this guarantee of remote summary routing.

Cortex's first MCP startup verifies the owned gateway and updates the effective
user `config.toml` with a local `cortex` model provider. HTTP/SSE and Responses
WebSocket requests use the same fixed loopback listener. The gateway establishes
TLS to `chatgpt.com` and keeps authentication in transit only. WebSocket framing
and handshakes are separate on each leg, with compression disabled; message
content, application headers, ping/pong and close behavior are relayed.

Codex applies the route when it next loads configuration. Installing a plugin
does not guarantee that MCP has started or that an existing task has reloaded
its provider. A new task or Desktop restart can still be necessary; Cortex
does not interrupt active tasks. A healthy gateway alone does not prove client
routing: verify fresh model-request outcomes from that exact client.

Automatic setup requires the normal OpenAI provider. It refuses to replace a
custom provider or custom upstream. Subsequent user provider edits take
precedence. A private route journal records the fields Cortex owns, and
`config.toml.cortex-backup` preserves the original file. Disabling the gateway
or the recorded Marketplace entry restores matching owned fields and drains
the gateway; unrelated later edits survive. Plugin removal is observed while
the independent gateway is running. If it was forcibly killed first, its
observer cannot perform cleanup. Configuration already loaded by an active
client changes only on that client's next configuration load.

Codex's installed source markers identify the remote V2 compactor in
`core/src/compact_remote_v2.rs` and its WebSocket request builder in
`codex-api/src/endpoint/responses_websocket.rs`, with the request schema fields
`response.create`, `input`, `reasoning`, `store`, and `stream`. When that V2
request is sent as an uncompressed direct `response.create` message containing
an input item whose type is `compaction_trigger`, the gateway receives the bounded
message and rewrites only `model` and `reasoning.effort`. `store`, `stream`,
and all other JSON fields are preserved. A direct HTTP V2 request with the same trigger follows the
existing HTTP policy, which also applies its upstream-required storage/stream
normalization.

The upstream Codex sources make the automatic route more precise: `core/src/compact_remote_v2.rs`
sets `CompactionTrigger::Auto` for `run_inline_remote_auto_compact_task`, while
`core/src/compact_remote_v2_attempt.rs` appends `ResponseItem::CompactionTrigger {}`
and calls `ModelClientSession::stream`. The shared
`codex-api/src/endpoint/responses_websocket.rs` serializes that request as
`ResponsesWsRequest::ResponseCreate` and sends it as a WebSocket text message.
Thus automatic compaction reaches the same outbound `response.create` WebSocket
handler as other V2 requests; it does not use `/backend-api/codex/responses/compact`.
The installed binary is stripped, so these source links establish the trigger
and handler shape while its exact threshold branch remains unverified locally.

Automatic compaction and a standalone `/compact` action can converge on the
same remote V2 `response.create`/`compaction_trigger` wire shape. The gateway
therefore identifies the protocol trigger, not the origin story: its bounded
log records `auto_compaction` for the HTTP V2 classifier and
`websocket_compaction` for the WebSocket frame classifier. To distinguish
automatic from standalone behavior without another submission, correlate
those sanitized entries with local lifecycle telemetry: a preceding native
`/compact` user message indicates standalone steering, while a threshold-driven
compaction has no such user message. Network evidence alone cannot make that
distinction, and a failed helper receipt must not be retried for this purpose.

For Marketplace installation, the gateway starts by default. The packaged
Python entrypoint creates the private config, installs
the hash-locked runtime into `$CODEX_HOME/cortex/deps/<lock-digest>`, and ensures the cached
gateway and provider; failures abort startup. A verified dependency tree is reused
by subsequent MCP processes, including workers. Set `gateway.enabled = false` in
`$CODEX_HOME/cortex/config.toml` for explicit storage-only mode.
`scripts/cortex-dev` remains the isolated developer launcher,
uses the same automatic provider code in its disposable profile. The proxy accepts only loopback listener
authorities and the fixed upstream. Logs contain bounded model/effort,
request-kind, and status metadata only: cookies, authorization values, request
bodies, and WebSocket payloads are never written to diagnostics.

The `/backend-api/codex/responses/compact` handler is retained solely as a
compatibility mapping for older manual callers: decodable JSON is routed to the
supported V2 path, while opaque legacy bytes remain byte-for-byte pass-through.
It is not a second WebSocket route or a fallback retry. The HTTP proxy module
serves HTTP, health and WebSocket traffic on the provider listener.
Every proxied request emits bounded, secret-free model/effort and request-kind
telemetry to the private rotating gateway log; malformed ordinary or opaque
legacy bytes remain unchanged. The proxy preserves path/query, body bytes, and
all non-authority header pairs; it rewrites only the local incoming `Host`
authority to the configured upstream authority on the upstream wire so
Cloudflare virtual-host routing succeeds. This is transport metadata, not a
semantic body rewrite. Compaction alone rewrites `model` and
`reasoning.effort`. The outbound HTTP client does not inject an
`Accept-Encoding` header when the caller omitted it, preventing an unsolicited
encoded model catalogue; an explicit caller value is forwarded unchanged.
Automatic response decompression remains disabled, so upstream response headers
and body bytes are streamed to the caller as received.

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

> **Phase 2 status (2026-09-11): CANCELLED by the user.** The outcome/efficiency
> comparison is non-blocking and non-scoring for the stable 1.15.9 release. Do
> not start a Phase 2 comparison or claim efficacy, scoring, or promotion; the
> adapter details below are retained as historical/offline reference.

Use only `./scripts/cortex-live-smoke start --workdir PATH` for the exact
`cortex-markdown-smoke` session on the default tmux server. Inspect `capture` and
`status`, visibly confirm trust before one explicit `enter`, and confirm the
composer. Compare the passive initialization receipt with the isolated candidate
and seven-tool catalogue. Workloads begin with `$cortex:orchestrator` followed by
ordinary product work. `send --prompt-file FILE` pastes once, waits five real
seconds and sends one named Enter; it requires an ordinary before/after native input
receipt but no Phase 2 control, session, or trust receipt. The sealed Phase 2 adapter
injects an explicit control marker and selects the stricter trust/send protocol;
incidental or missing session fields never switch or weaken either mode.
Ordinary `send` is also single-use: a non-null first-submission timestamp refuses
before any composer/receipt inspection, state write, paste, or Enter. A resumed
session observes and continues its existing task without replaying that request.

Each new CLI smoke run uses the exact project-local store at
`PATH/.codex/cortex/cortex.sqlite3`. A `--resume-last` run reuses that same
canonical project store and rejects a missing or mismatched store. `stop` keeps the
project store for resume and removes only the session and observation streams.
For a new evaluation cell, add `--evaluation-fresh-store`: the opt-in Linux-only
CLI guard requires a supported architecture/syscall and same-filesystem staging,
builds the complete owner-private `.codex/cortex` hierarchy in an external
same-filesystem stage, and installs it once as the project `.codex` destination
with no-replace semantics. Unsupported, unavailable, raced, or otherwise
unproven conditions fail closed before canonical project mutation; uncertain
loser stages are retained outside the project rather than removed by pathname.
Git/config readiness is completed before that commit. It requires the
project-local SQLite target to be absent and non-redirected, leaves database
creation to the normal initializer, and records only privacy-safe path/check/digest
provenance. It never deletes, truncates, migrates, overwrites, or accepts an
existing store. The flag does not alter ordinary starts or explicit
`--resume-last` behavior.
The sealed Phase 2 compatibility adapter additionally passes that exact empty
project directory as `CORTEX_DATA_DIR` to both frozen launchers after removing any
ambient value. This lets the format-10 baseline use the same isolated path that the
format-11 candidate resolves from native project state; it does not expose or reuse
the isolated home's unrelated Cortex database.
The adapter also revalidates the selected archive's full normalized payload digest
and exact embedded plugin version before every command. Preflight success cannot be
reused after archive rebuild or mutation drift; an identity contradiction stops the
cell before the launcher or fresh-store commit.
Phase 2 cell evidence is collected through the adapter's single sealed
`collect-evidence --evidence-dir PATH` command. It owns the bounded observer
arguments, atomically records status, capture, events, calls, usage, and audit, and
creates a final bundle receipt only after validation. Adapter-mediated `stop`
requires that matching complete bundle, preventing partial capture from deleting
the transient streams. The exact invocation is documented in
[`docs/project/phase2-external-evaluator.md`](docs/project/phase2-external-evaluator.md).

The control also seals one common `cortex-desktop-dev` observer beside
the common harness and applies it to both archived arms. Its minimal runtime
bundle contains the exact `profiles.json` plus the complete dynamically addressable
`skills/` tree; the control records every canonical relative path and file hash as
one trusted aggregate. Preparation, independent audit, and adapter preflight reject
missing, changed, extra, symlinked, non-private, or relocated dependencies before
live transport. Participant usage is
content-free and scoped to the single observed coordinator root; an unavailable
usage status remains availability evidence and is never converted into a quality
score. Compound command failures name the executable identified by the shell's
command-not-found receipt, rather than the first command in the compound string.
The observer preserves the raw exit-1 receipt while classifying only a simple
`rg`/`grep` search, or an exactly proven `pwd && rg/grep` search, with empty stderr
and empty search output as semantic `no_match`. That expected search absence is
excluded from host-failure and tool-error quality gates. Exit 2+, stderr, extra
shell segments, mismatched `pwd` output, and every other failure remain blocking;
only an exit-127 availability failure may be corrected by a later family success.
The shared audit classifies evidence integrity before quality: truncated,
unbound, assignment-unverified, contaminated, or topologically ambiguous rows
make a cell unscoreable, while complete attributable failures remain visible as
quality findings in an otherwise valid bundle. Raw failure and policy receipts
are always preserved, and the same decision table applies to both arms and hosts.
Every cell's sealed `start` owns a bounded trust/composer transition and returns
`ready-for-begin` before `begin-cell --cell-id cell-NNN`. It waits through an early
loading placeholder, sends at most one named Enter only while the exact trust
prompt is active, seals the control/session result, and proves the exact empty
composer before cell authorization can be issued. A separate `accept-trust` is
idempotent receipt verification only and emits no input. Empty means the latest
rendered `›` input row is the exact placeholder; a historical placeholder above
active unsent text is never readiness evidence.
Once tmux has returned the exact owned pane identity, start captures one immutable,
control-scoped session-binding receipt and marks the lifecycle started. Begin-cell
refuses before that transition or after receipt/pane-field rotation. Cell
authorization files are scoped by both control digest and session receipt, so a
fresh control cannot inherit a prior UID-global or cross-control authorization;
an unconsumed authorization for the current session remains exclusive. That
boundary issues a one-time nonce and binds the later submission, evidence
bundle, and stop to the current workdir, store, session receipt, coordinator thread,
control, and arm. Cross-cell replay, duplicate submission, and duplicate stop are
refused even when control and arm are unchanged. The adapter durably changes the
authorization from `issued` to `submitting` before transport. Only an observed exact
root-thread native user-turn receipt can atomically promote it to `submitted`, with
the prompt digest, thread ID, user-turn source/line hashes, finite submission
timestamp, and nonce-bound receipt recorded together. The native source must be the
single rollout file held open by the exact owned Codex descendant after tmux,
pane-PID, and process-start revalidation. The harness reads the inode identified by
that descriptor and requires a second identical descriptor/process/session snapshot
immediately before acceptance; added, removed, retargeted, or ambiguous rollout FDs
fail closed. Same-workdir/time rows alone cannot match.
A bounded `resolve-send` can
reconcile a delayed accepted receipt without sending input. Failed or uncertain
sends remain non-resubmittable, and submitted-or-later reads
revalidate timestamp/receipt and transport-state consistency before collection or
cleanup.
An unchanged pre-submit trust prompt or a proved empty composer after an unaccepted
send has one separate abort route. The adapter
requires the exact control/session binding, two stable owned process/activity
snapshots, either a null request or the matching no-submit prompt digest, null
submission receipts, and no task, worker, foreign call, or
open exec state before persisting a one-time abort receipt and issuing exactly one
exact-target interrupting stop. The cleanup helper receives the sealed tmux server
PID, session name/ID/creation time, pane ID/PID/process-start identity, control
digest, and session receipt; it revalidates them at the cleanup boundary and
addresses only the sealed pane/session IDs. It is not orphan recovery; any uncertainty is non-retryable,
and any submitted session is refused.
The collected authorization also seals the canonical evidence-directory path and
its device/inode, owner, and private mode. Stop refuses copied, renamed,
symlink-aliased, or non-canonical directory paths even when bundle bytes match.
Normal Phase 2 stop remains bundle-gated. Normal collection accepts a stable idle
interactive composer only when its exact owned Codex process and task trees match,
all workers and the coordinator are terminal, the final call/event join has the
same owned report and draft identities, each independently validated as a nonempty
typed `r_[0-9a-f]{12}` or `d_[0-9a-f]{12}` receipt before equality, the recognizable composer footer matches
model, effort, and workdir, no tool/exec/wait/session activity remains, every wait
has an explicit successful completion receipt, and repeated status, process, capture, events,
calls, and audit snapshots are identical. This normal path needs no exit marker;
bundle-authorized `stop --interrupt` then closes only that accepted idle session
through the same exact-target helper. A replaced server, renamed/recreated session,
replacement pane, or reused pane PID is refused before signaling, and a failed
exact stop does not consume the cell authorization.
The retained bash path still requires one successful `Cortex live-dev exit=N`
marker and no descendants. The
distinct `recover-orphan` route is
limited to exact owned `issued`/`submitting` sessions with no submission receipt,
requires explicit one-time authorization, and captures terminal status/output,
calls, events, and audit. Every live pane is refused. A retained dead `bash` pane
must have a matching `Cortex live-dev exit=N` marker, no active work, and the same
session receipt, status, process snapshot, and marker capture at a final pre-stop
recheck; both evidence generations are hashed. Submitted, receipt-less,
repeated, raced, or foreign sessions are refused. It writes an external consumed
cleanup receipt before a new disposable cell may start.
If the whole tmux server is already gone, `gc-absent-runtime` is the only
stale-state path. It inventories recognized control transactions, session
bindings, authorizations, saved session state, and invalidation receipts; refuses
unknown or foreign generations; proves tmux and every sealed process identity
gone; and requires retained calls/audit evidence with no active task, tool, wait,
or session; both audit open-state fields must be present as literal empty lists,
and simultaneous saved-session files are refused. The strict state-root allowlist
accepts only canonical filenames derived from the selected
control/session/cell provenance; any extra JSON, backup, hidden/temp, case variant,
alternate suffix, duplicate, or unrecognized entry refuses before mutation. The
binding's exact `events` path must be the canonical direct child of the state
root. After runtime loss it may be absent; if present it must be an owner-owned
mode-0700 nonsymlink directory with stable device/inode identity and no children.
A file, symlink, wrong owner/mode, opaque or valid-looking row, hidden file, nested
entry, or any other content refuses. Event rows are never accepted as GC evidence.
It repeats the complete
tmux/session/PID/start-tick/activity/inventory/hash proof immediately before every
archive rename and before consumption. A durable
prepared-to-consumed receipt makes the same nonce idempotent and permits only a
byte-identical, fully revalidated partial-archive resume. Existing or ambiguous runtimes are never
garbage-collected.
The same command has a distinct pre-binding failed-launch branch. Only paired
`failed` transactions and paired `unusable` failure receipts with
`session_cleanup: stopped` qualify, and their control/workdir identities must
match canonical project markers. It proves the project store, binding,
authorization, submission, and runtime events absent; accepts only an empty
capture plus one sealed launcher-provenance row; and archives the seven exact
kind/digest-sealed entries with full rechecks. Partial, successful, mixed, extra,
malformed, active, or raced generations refuse.
Validated consumed archives are inert history during classification, so a later
bound stale generation routes to normal GC. An exact old nonce replays only its
matching consumed receipt; simultaneous live bound and failed-launch markers
remain an ambiguous refusal.
The successful atomic hierarchy commit is the fresh-store acceptance boundary.
Git-root and other rejection-prone launch checks run before it. A no-workload
tmux preflight creates, verifies, configures, and removes the exact isolated
session first, using the same sealed `new-session` arguments as the real launch
except for its bounded command. Server/session/pane provenance comes from one
exact-target `list-panes` result, avoiding target-type-dependent empty format
values. Probe and launch configuration use the returned session ID, pipe and
dispatch use the returned pane ID, and identity rechecks refuse recreation before
mutation or cleanup. Before rename,
independent control/workdir transaction journals are durably `prepared` outside
the project and the same transaction is embedded as a `committing` marker in the
staged hierarchy. A crash or failure writing later receipts therefore still leaves
fail-closed reuse evidence. Post-commit failures are reported as the explicit
non-qualifying `post_commit_launch_failure`; diagnostic failure receipts and an
unusable marker are best-effort redundant records, not the sole guard. The sealed
control and workdir cannot be resubmitted, and any newly created tmux session is
stopped by its exact returned session ID. No evaluation acceptance is recorded;
the next attempt requires a new control and a different disposable workdir.
The CLI helper requires `--workdir` to already be the root of a Git repository and
does not initialize it or write Git identity settings. For an isolated run, create
a fresh empty directory with `mktemp -d` and run `git init` explicitly first. A
worker Git probe in an uninitialized workspace remains advisory observer noise.

Inspect complete `calls` and `events`, including after a discovered fault, and run
`audit` before stopping. A command wrapper must expose its exit status or running
session receipt. Collect the sealed evidence bundle from either the proven idle
composer or the owned idle bash with its successful exit marker, then stop only
that exact session; use bundle-authorized `stop --interrupt` for the idle composer
or after a failed run. Never kill the tmux server.
Resume uses the same workdir and `--resume-last`, with the existing task confirmed.

Real Desktop uses `scripts/cortex-desktop-dev start --workdir PATH --prompt-file FILE`
and the same isolated candidate in a disposable Electron profile. Confirm the
prepared composer, then `send` focuses that exact window, submits with Ctrl+Enter
and requires one new task receipt. Review its `calls`, `events` and `audit` too.
The Desktop helper applies the same existing-repository validation without
initializing the directory or writing Git identity, and treats a worker Git probe
without a repository as advisory.
Use ordinary hook trust; never bypass it for qualification. CLI/Desktop parity
requires consecutive successful runs on one unchanged payload. Unavailable hosts
and unrun checks remain unverified.

The offline Phase 5 adaptive overlay remains advisory and fail-closed: candidate
evidence must join selected report metadata, malformed proposals retain the static
baseline, and recommendation reason/JSON sizes are bounded.

See [verification](docs/project/verification.md),
[host compatibility](docs/project/host-compatibility.md),
[lifecycle hooks](docs/features/lifecycle-hooks/index.md),
[comparative evaluation](docs/project/quality-evaluation.md), and
[Phase 4 telemetry](docs/project/phase4-telemetry.md), and
[Phase 5 adaptive selection](docs/project/phase5-adaptive-selection.md), and
[current release evidence](docs/release-readiness.md).

Live development uses Luna/high for the coordinator and Luna at medium/high for
native workers. The isolated helpers layer this user-requested test policy and audit
actual participant/selector receipts. Heavy live-test models are rejected. This does
not change stable settings or the plugin's general user-selected model policy.

Workers keep routine checks non-destructive: Python checks use the inherited
`PYTHONDONTWRITEBYTECODE=1` setting, and workers do not use recursive cache cleanup,
`git clean`, reset/checkout, or similar commands just to remove residue. Existing
residue is left in place and reported as a blocker when it prevents a check; host
permissions remain native and no guidance auto-approves a destructive command.
After a pending or timed-out `wait_agent`, the coordinator repeats a bounded wait for
the same owner and may inspect status or selected evidence internally, but does not
send `send_message`/`followup_task` solely because the wait produced no evidence.
Only an inbound same-owner handoff reply, direct user steering/clarification, or an
intentional follow-up after reconciling terminal worker evidence is a legal transition.
