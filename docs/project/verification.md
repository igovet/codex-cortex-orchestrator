# Verification

The control plane is validated with the standard-library test suite:

```bash
python3 -m unittest discover -s tests -v
python3 scripts/cortex-cold-boot-smoke.py
python3 scripts/cortex-luna-high-eval.py
python3 scripts/cortex-composite-benchmark.py --workers 8 --waves 5
python3 scripts/probe-fresh-cortex-plugin.py
python3 scripts/verify-cortex-release.py --require-tracked
python3 -m py_compile plugins/cortex/scripts/cortex.py plugins/cortex/scripts/cortex_hook.py scripts/cortex-cold-boot-smoke.py scripts/cortex-luna-high-eval.py scripts/probe-fresh-cortex-plugin.py scripts/validate-cortex-marketplace.py scripts/verify-cortex-release.py tests/jsonrpc_harness.py
bash -n scripts/sync-cortex.sh
./scripts/sync-cortex.sh --check
./scripts/sync-cortex.sh --dry-run
```

Current Cortex 3.2.1 evidence:

- Plugin cachebuster `3.2.1+codex.20260814203024` installed successfully.
- `./scripts/sync-cortex.sh --check` passed with installed content verified and
  `agents.default_subagent_model=gpt-5.6-luna`.
- The full Python suite passed 220 tests; marketplace validation also passed.
- Cold-boot smoke passed with seven `continue_orchestration` calls, eight
  reports, and a parallel wave observed.
- Deterministic Luna-high fixtures passed for `automatic_sequential`,
  `compact_parallel`, and `blocked_resume`. The live route was not attempted
  because `--live` was not supplied; no live-model evidence is claimed.
- The composite benchmark met its target: 50 legacy calls versus 22 relative-v3
  calls, a 0.56 reduction. The v3 count includes one scoped report write and
  one coordinator report read for each of eight workers.
- The isolated fresh-plugin probe passed with version
  `3.2.1+codex.20260814203024`; Python compilation, shell syntax, and installer
  dry-run also passed.
- `verify-cortex-release.py --require-tracked` remains correctly blocked because
  committed `HEAD` does not contain the uncommitted 3.2.1 package contract.
  No commit, tag, push, catalog submission, or publication is verified.

`cortex-cold-boot-smoke.py` is the v3 cold-boot smoke. It creates a fresh
temporary Git project, drives the stdio JSON-RPC server through the public
lifecycle and scoped `record_report`/`read_worker_report` tools, restarts the
server mid-lifecycle, and proves one start/continue per wave plus compact report
transport.
It covers server-owned idempotent replay, relative steps and parallel worker
slots, strict eight-field `cortex/report/v1` reports, report and manifest
reconciliation, documentation decision, handoff, audit, and final completion.
Focused unit
regressions cover changed-payload conflicts, validation before writes,
future-wave replacement/rework protection, every transaction checkpoint,
multi-root isolation, and expected `ok: false` results that do not enter the
exception log.
For the current candidate it completed successfully with seven continue calls,
eight reports, and a parallel wave.

Coordinator-isolation regressions assert that `SessionStart` reasserts the
root lock and that every public v3 `next_action`, including validation
failures, tells the root to avoid project inspection, edits, builds, and tests,
dispatch only workers, and remain idle while they run. The installable skill
contracts are also checked for the same coordination-only boundary.

The question regression drives `manage_orchestration(intent="question")`
through stdio JSON-RPC with the modern `extensions["openai/form"]` host
capability enabled and proves that the server emits `elicitation/create` in
`openai/form` mode and returns the answered result. The legacy capability bit
remains covered for compatibility.
This verifies the MCP/UI protocol path; a host without that capability is
reported as unsupported and is not counted as a successful UI interaction.
The Luna-high evaluation fixture covers sequential, compact parallel, and
blocked/resume or reassessment scenarios. Its live harness is release evidence
only when the Codex runtime is available; `SKIP` records missing live evidence,
not a pass.
The current deterministic run passed `automatic_sequential`,
`compact_parallel`, and `blocked_resume`; live execution remains unattempted
because `--live` was not supplied.

On 2026-08-14 the live harness completed its three isolated scenarios in
separate runs using the exact parent launch route `gpt-5.6-luna` with reasoning
effort `high`. Sequential, compact-parallel, and future-wave reassessment all
finished with one active task, public v3 tools only, strict reports, server-
observed close evidence, and handoff. The Codex JSON event stream did not expose
an independent effective-model field, so the exact launch configuration is
evidence of the requested runtime route, not a separate host-model attestation.
This is historical runtime evidence and was not rerun for the current 3.2.1
candidate.

`cortex-composite-benchmark.py` is a call-count contract benchmark, not a
latency benchmark. It reports `legacy_mcp_calls` against
`relative_v3_mcp_calls` and checks the façade target of
`waves + 1 + 2 * workers` calls: one start, one continue per wave, and one
report write/read pair per worker. Native host spawns are intentionally
excluded because they remain host calls rather than MCP façade calls. Run it
when changing the public lifecycle; it is not correctness or performance
evidence.
The current run reported 50 legacy calls and 22 relative-v3 calls, meeting the
target with a 0.56 call-count reduction.

Facade validation regressions cover malformed requests and completions,
including missing/relative roots, malformed compact waves, invalid reports,
stale steps, and slot mismatches. They assert one recoverable
structured `ok: false` result containing all independent preflight
diagnostics, no partial attempt transition after preflight failure, and a
corrective `next_action` that preserves the coordinator lock. Expected
`ok: false` results must not create
`~/.codex/logs/cortex-tool-errors.jsonl`; raised MCP exceptions remain covered
separately and must produce a redacted entry.

`probe-fresh-cortex-plugin.py` copies the full root-layout checkout into a
temporary directory, uses temporary `HOME` and `CODEX_HOME`, installs it with fresh Codex CLI
processes, and checks that `cortex` is exposed. It reports
`SKIP` when the Codex CLI is unavailable; treat that as an environment
limitation, not plugin-registration evidence.
The current isolated probe passed and observed installed version
`3.2.1+codex.20260814203024`.

The lane regression creates a real temporary Git repository, materializes a
declared branch/worktree, reconciles branch and dirty state, and retires the
clean worktree without force removal.

Activation regression coverage proves that mutations fail before explicit
skill-route activation. It also verifies that the skill normalizes activation
to the MCP server's canonical internal `/cortex` token and deactivation to its
canonical `/normal` token; neither is a promised host slash command. Activation
becomes bound to the initialized task and active-thread mapping. Completing a
task cleans that mapping, not the activating principal's session. The v7 suite
also covers principal-bound classification and recoverable stale status/revision hints,
manifest reconciliation, global resource claims, numbered task/hook resolution,
and lane lifecycle safety.

The JSON-RPC regressions distinguish protocol correction from server failure:
structured public v3 `ok: false` results leave no exception log, while raised
MCP-boundary exceptions create a redacted
`~/.codex/logs/cortex-tool-errors.jsonl` record. Exception records retain the
chat/thread session id, request id, and available task/attempt ids with
restrictive file permissions.

Pipeline classification regressions prove that the bounded compatibility aliases
`planning`, `discovery`, and `verification` normalize to the canonical `plan`,
`discover`, and `qa` gate IDs in both the pipeline and parallel-group waves;
`implement` normalizes to `implementation`, and `build_verification` to final
`close`. Cross-wave duplicates of one canonical phase are rejected, while
unrecognized gate IDs remain hard validation errors.

Public-facade regressions require exactly five listed tools: the three
coordinator lifecycle operations, worker `record_report`, and coordinator
`read_worker_report`. They verify that the public continue schema advertises
`report_ref` rather than an inline report body, that the coordinator can read
and advance with a persisted exact eight-field report, and that inspect returns
`available_reports` when native acknowledgement is interrupted after
persistence. Prompt-contract regressions require the worker's compact
`REPORT_RECORDED report_ref=<value>` final and report-tool error fallback.
Pipeline regressions also require coordinator authority in the returned
snapshot and an explicit reason for a future-wave replacement; planner and
explorer evidence remains advisory.

Codebase Memory is enabled for the local development host and this repository
has a ready index. Prompt regressions verify conditional exact-root lookup,
graph/architecture/trace preference, source/test confirmation, one bounded
refresh for designated discovery profiles, and a non-looping fallback. A real
`explorer` forward-test independently completed `list_projects`, `search_graph`,
and `get_code_snippet`, resolved the exact project and `host_spawn_prompt`
symbol, and confirmed both Codebase Memory and compact report-ack guidance in
the generated prompt. The coordination-only root exclusion remains explicit.

Profile-routing regressions validate the exact 21-name public enum, reject an
unsupported phase/profile assignment before any task directory is created,
and cover the ordered specialist implementation rules plus the conservative
`general` fallback. Fixtures exercise English and Russian signals across
objective, requirements, acceptance criteria, scope, allowed paths, and
verification. They also verify that dispatches expose phase, profile,
capability, sandbox, and selection rationale without changing native call
arguments, and that planner/explorer prompts receive the complete generated
team catalog.

The isolated route regressions parse the canonical bundled skill contract and
exercise fixture documentation trees. They prove help is read-only, an
incremental harvest changes only evidence-justified generated facts while
accounting for its manifest, and a refresh preserves manual notes and produces
an idempotent second pass.

The v7 report regressions cover strict shape and redaction, task/attempt scope,
one-use evidence receipts, explicit context grants, idempotent submissions,
concurrent publishers, generated-Markdown repair, and capability-aware routing.
Host-spawn binding regressions also prove that a model-routed attempt cannot
become `running` without the host's actual `host_model`, and that an expected
model mismatch (for example, configured-default Luna but Terra started) is terminalized
as `host_model_mismatch` instead of being accepted as a successful dispatch.
Routing regressions also cover the exact five-pair model/effort remapping table,
including Sol routes, and prove the three hidden Luna resolution branches:
confirmed configured default with no native `model`, explicit Luna when the
host advertises it, and explicit hidden Terra when it does not. No automatic
`create_thread` fallback is accepted.
The repository validator additionally checks that all installable sources live
under `plugins/cortex/`, `profiles.json` matches exactly 21 profile files,
every TOML identity/description/sandbox matches its canonical entry, complete
route metadata is present, all eight implementation specialists occur exactly
once before the `general` fallback, and the generated root-skill catalog is
byte-for-byte synchronized. It also requires exactly 10 skills, all eight
report fields, and no retired `task_formatter`/dedicated-orchestrator profile.

The installer regression also checks atomic global `[agents]` default creation,
private configuration backups, explicit override preservation, dry-run output,
replacement of a different default while preserving comments and mode, and
read-only check failures when the setting is missing or not Luna.

The 2026-08-14 fresh-CLI proof used parent task
`01a000fb-12ef-7631-a022-8076b6b6a828`. The first Cortex delegation selected
`expected_model=gpt-5.6-luna`, `model_resolution=configured_default`, and
`reasoning_effort=high`, with no `model` field. The persisted parent rollout
likewise records a native `spawn_agent` call containing `task_name=explorer`
and `reasoning_effort=high` but no `model` key. Its child edge points to
`01a000fb-cd55-76c1-8922-484c710f6d6e` with `thread_source=subagent`; that
child's persisted `thread_settings_applied` snapshot records
`model=gpt-5.6-luna` and `reasoning_effort=high`. This runtime metadata, rather
than worker self-report, is the acceptance evidence. The worker returned to
the parent and no user-owned visible task was created.
This proof predates the current 3.2.1 candidate and is not a fresh-install or
runtime attestation for this release.

`verify-cortex-release.py --require-tracked` is the blocking release boundary:
it validates a fresh `git archive HEAD` rather than the mutable working tree,
and rejects nested marketplace artifacts, symlinks, ledger state, bytecode,
secret-prone filenames and credential-store paths, missing public release
policies, private local home paths in public release files, and release
placeholders. Its regression fixture proves `.env`, `.env.*`, private-key,
credential-file, and SSH-key paths are rejected while ordinary Markdown that
documents secure configuration remains allowed.

It requires a committed `HEAD`. Without one, the non-blocking command reports
`SKIP` and `--require-tracked` fails intentionally; neither result validates a
release archive. Create the initial commit only with authorization and rerun
the blocking command against the committed tree before publication.
For the current candidate, `--require-tracked` is blocked exactly because the
committed tree does not yet contain the uncommitted 3.2.1 package contract; this
is not release or publication evidence.

<!-- GENERATED:START -->

## Authoritative command inventory

- `python -m unittest discover -s tests -v` — standard-library regression suite; CI source: [cortex.yml](../../.github/workflows/cortex.yml).
- `python3 scripts/cortex-cold-boot-smoke.py` — black-box JSON-RPC lifecycle smoke test; CI source: [cortex.yml](../../.github/workflows/cortex.yml), implementation: [cortex-cold-boot-smoke.py](../../scripts/cortex-cold-boot-smoke.py).
- `python3 scripts/cortex-luna-high-eval.py` — mandatory deterministic Luna-high fixture scenarios; add `--live` for release evidence from a real `gpt-5.6-luna` high parent. `SKIP` is not live pass evidence; CI source: [cortex.yml](../../.github/workflows/cortex.yml).
- `python3 scripts/cortex-composite-benchmark.py` — v3 MCP call-count contract benchmark; implementation: [cortex-composite-benchmark.py](../../scripts/cortex-composite-benchmark.py). It asserts one start plus one continue per completed wave and makes no latency claim.
- `python3 scripts/probe-fresh-cortex-plugin.py` — isolated fresh-plugin registration probe; CI source: [cortex.yml](../../.github/workflows/cortex.yml), implementation: [probe-fresh-cortex-plugin.py](../../scripts/probe-fresh-cortex-plugin.py). `SKIP` means the Codex CLI is unavailable.
- `python3 scripts/verify-cortex-release.py --require-tracked` — blocking tracked-release archive boundary; CI source: [cortex.yml](../../.github/workflows/cortex.yml), implementation: [verify-cortex-release.py](../../scripts/verify-cortex-release.py).
- `python scripts/validate-cortex-marketplace.py` — repository marketplace and plugin-contract validation; CI source: [cortex.yml](../../.github/workflows/cortex.yml), implementation: [validate-cortex-marketplace.py](../../scripts/validate-cortex-marketplace.py).
- `python -m py_compile plugins/cortex/scripts/cortex.py plugins/cortex/scripts/cortex_hook.py scripts/cortex-cold-boot-smoke.py scripts/probe-fresh-cortex-plugin.py scripts/validate-cortex-marketplace.py scripts/verify-cortex-release.py tests/jsonrpc_harness.py` — Python syntax compilation for runtime and helper modules; CI source: [cortex.yml](../../.github/workflows/cortex.yml).
- `bash -n scripts/sync-cortex.sh` — shell syntax check; CI source: [cortex.yml](../../.github/workflows/cortex.yml).
- `./scripts/sync-cortex.sh --check` — read-only installed-content/legacy-artifact check; source: [sync-cortex.sh](../../scripts/sync-cortex.sh).
- `./scripts/sync-cortex.sh --dry-run` — no-write report of the planned installation and managed legacy cleanup; source: [sync-cortex.sh](../../scripts/sync-cortex.sh).

<!-- GENERATED:END -->
