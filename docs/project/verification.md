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

Recorded Cortex 4.0.4 source evidence:

- The full Python suite passed 251 tests.
- File-size hardening passed: ordinary JSON writes use the bounded
  `MAX_JSON_BYTES=8 MiB` limit with fail-before-replace diagnostics; manifests
  use `MAX_MANIFEST_BYTES=64 MiB`; baseline preflight runs before task-directory
  creation; and handoff/reconciliation snapshot serialization remains bounded.
  Oversized artifacts fail closed with an actionable error rather than
  surfacing at close.
- A migration exercised on a copy compacted a 291212-byte legacy registry to
  9624 bytes.
- The generated Planner prompt measured 13679 bytes.
- Installed `cortex@cortex` as `4.0.4+codex.20260815083316` from the local
  marketplace. Installed content matches the source for the manifest,
  `scripts/cortex.py`, orchestrator/cortex-control skills, and planner profile.
  `./scripts/sync-cortex.sh --check` passed; its dry-run preserved
  `default_tools_approval_mode=approve`, and the exact user configuration
  section/value was confirmed.
- Plugin and marketplace validation, Python compilation, and shell syntax
  checks passed. Cold-boot smoke passed with seven continue calls, eight
  reports, and a parallel wave. Deterministic Luna fixtures passed; live mode
  was skipped because `--live` was not supplied. The 8-worker/5-wave composite
  benchmark passed with 22 public MCP calls versus 50 legacy calls (56%
  reduction), and the isolated fresh-plugin probe passed on 4.0.4.
- The tracked-release archive check remains blocked because the 4.0.4 tree is
  not committed; the current `HEAD` still contains the previous manifest. No
  release commit was created.
- No live-model, commit, tag, push, catalog submission, or publication is
  verified.

Historical 4.0.0 evidence includes 241 passing tests in 15.770 seconds;
changed-skill, plugin, and marketplace validation; Python compilation and shell
syntax; installed and content-verified cachebuster
`4.0.0+codex.20260814231427`; installer check/dry-run; cold boot; deterministic
fixtures; the composite benchmark; isolated fresh-plugin probe; and the
installed intent-hold probe. Live execution was skipped because `--live` was
not supplied. These results do not attest 4.0.2.

Historical 3.3.0 evidence includes installed and content-verified cachebuster
`3.3.0+codex.20260814224159`, 237 tests, changed-skill/plugin/marketplace
validation, Python compilation, shell syntax, installer check/dry-run, cold
boot, deterministic fixtures, the composite benchmark, and the isolated
fresh-plugin probe. These results do not attest 4.0.2.

Historical 3.2.1 evidence includes installed cachebuster
`3.2.1+codex.20260814203024`, content-verified installer check with the Luna
default, 220 tests, marketplace validation, cold boot, deterministic fixtures,
the composite benchmark, isolated fresh-plugin probe, compilation, shell
syntax, and installer dry-run. These results do not attest 4.0.2.

`cortex-cold-boot-smoke.py` is the v3 cold-boot smoke. It creates a fresh
temporary Git project, drives the stdio JSON-RPC server through the public
lifecycle and scoped `worker_question`/`record_report`/`read_worker_report` tools, restarts the
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
For the historical 4.0.0 candidate it completed successfully with seven
continue calls, eight reports, and a parallel wave. It has not been reported
for 4.0.2.

Coordinator-isolation regressions assert that `SessionStart` reasserts the
root lock and that every public v3 `next_action`, including validation
failures, tells the root to avoid project inspection, edits, builds, and tests,
dispatch only workers, and remain idle while they run. The installable skill
contracts are also checked for the same coordination-only boundary.

The question regressions drive `manage_orchestration(intent="question")`
through stdio JSON-RPC with only the opaque `question_ref`. Cortex resolves the
task, attempt, profile, and native-thread identity internally, emits one
`elicitation/create` request in `openai/form` mode, and returns the durable
answer. A duplicate call does not reopen the UI. Guessed identity fields are
rejected, and a host without native elicitation support leaves the question
open with an explicit no-prose-fallback action. Prompt coverage requires the
worker to end its current native turn in an idle/resumable state, the
coordinator to resume that exact worker through `followup_task`, and the worker
to poll the same ref before continuing the same attempt. The legacy capability
bit remains covered for compatibility.
The Luna-high evaluation fixture covers sequential, compact parallel, and
blocked/resume or reassessment scenarios. Its live harness is release evidence
only when the Codex runtime is available; `SKIP` records missing live evidence,
not a pass.
The historical 4.0.0 deterministic run passed `automatic_sequential`,
`compact_parallel`, and `blocked_resume`; live execution remains unattempted
because `--live` was not supplied.

On 2026-08-14 the live harness completed its three isolated scenarios in
separate runs using the exact parent launch route `gpt-5.6-luna` with reasoning
effort `high`. Sequential, compact-parallel, and future-wave reassessment all
finished with one active task, public v3 tools only, strict reports, server-
observed close evidence, and handoff. The Codex JSON event stream did not expose
an independent effective-model field, so the exact launch configuration is
evidence of the requested runtime route, not a separate host-model attestation.
This is historical runtime evidence and was not rerun for the current 4.0.2
candidate.

`cortex-composite-benchmark.py` is a call-count contract benchmark, not a
latency benchmark. It reports `legacy_mcp_calls` against
`relative_v3_mcp_calls` and checks the façade target of
`waves + 1 + 2 * workers` calls: one start, one continue per wave, and one
report write/read pair per worker. Native host spawns are intentionally
excluded because they remain host calls rather than MCP façade calls. Run it
when changing the public lifecycle; it is not correctness or performance
evidence.
The historical 4.0.0 run reported 50 legacy calls and 22 relative-v3 calls,
meeting the target with a 0.56 call-count reduction.

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
The historical 4.0.0 isolated probe passed and observed source version `4.0.0`.
The repository cachebuster was subsequently installed and content-verified as
`4.0.0+codex.20260814231427`.

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

Public-facade regressions require exactly six listed tools: the three
coordinator lifecycle operations, worker `worker_question` and `record_report`, and coordinator
`read_worker_report`. They verify that the public continue schema advertises
`report_ref` rather than an inline report body, that the coordinator can read
and advance with a persisted exact eight-field report, and that inspect returns
`available_reports` when native acknowledgement is interrupted after
persistence. They also prove the ref-only native question lifecycle described
above and that confirmed prune removes only stale task-scoped state. The 4.0.x facade
requires exact `task.user_request`, rejects coordinator-expanded
`task.objective` before ledger writes, and preserves the exact request in every
worker briefing. It deterministically holds short underspecified
product-surface creation requests until a blocking question is answered, while
detailed requests proceed without the automatic hold. Report regressions
require `questions: []`; material questions use `worker_question`, and
non-blocking evidence gaps use `uncertainty`. Prompt-contract regressions require the worker's compact
`REPORT_RECORDED report_ref=<value>` final and report-tool error fallback.
Idempotency coverage proves that the same exact `task.user_request` cannot
create a second active task when coordinator metadata or proposed waves differ,
and that replayed start and continue responses contain no dispatches. Successor
prompt coverage proves predecessor reports are passed as refs rather than
embedded bodies, then read through public `read_worker_report`. Pipeline
regressions also require coordinator authority in the returned
snapshot and an explicit reason for a future-wave replacement; planner and
explorer evidence remains advisory.

The current 251-test suite exercises opaque `task_ref` isolation for multiple and
concurrently started same-root tasks, same-request active-task replay, and
`needs_selection` when an ambiguous call omits the ref. They also exercise
phase-level `depends_on`, mandatory `Predecessor review:` evidence, public
report rejection for an incomplete acknowledgement, and fail-closed
ref-based predecessor access. Task and operation ledger reads enforce an
8 MiB file bound; migration coverage compacted a 291212-byte legacy registry
to 9624 bytes in a copy. The Planner prompt regression measured 13679 bytes
and remains below its bounded contract. Repository-knowledge cases verify automatic
index injection, compact-worker `context_files`, required `Knowledge reviewed:`
evidence, and rejection of unsafe context paths. Worker-report coverage also
requires safe project-relative `changed_files`; explanatory text belongs in
`findings` or `evidence`.

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
exercise fixture documentation trees. Existing evidence proves help is
read-only and preserves manual notes. Current source checks require both harvest
routes to use the full census pipeline, require a zero-gap coverage manifest
before incremental work, and validate the mandatory inventory, coverage
matrix, feature-page depth, independent completeness review, zero unexplained
unmapped surfaces, and no-change refresh pass. They also reject shallow feature
indexes missing Coverage matrix columns, Inventory totals, Unmapped surfaces,
Exclusions, or Known unknowns. These assertions are included in the current
251-test result.

The v7 report regressions cover strict shape and redaction, task/attempt scope,
one-use evidence receipts, explicit context grants, idempotent submissions,
concurrent publishers, generated-Markdown repair, and capability-aware routing.
Host-spawn binding regressions also prove that a model-routed attempt cannot
become `running` without the host's actual `host_model`, and that an expected
model mismatch (for example, configured-default Luna but Terra started) is terminalized
as `host_model_mismatch` instead of being accepted as a successful dispatch.
Routing regressions cover the simplified policy: explorer-only Luna selection,
exact Luna `max` defaults for planner and ordinary profiles, normal Terra
selection from `medium` through `max`, security Sol complexity floors, a hard
`max` ceiling for every route, and
matching `user_requested_model` provenance for non-security Sol. They also
prove configured-default Luna with no native `model`, explicit Luna when the
host advertises it, and hidden Terra fallback with preserved effort when Luna
is unavailable. Removed `sol_escalation`, failed-Terra/auditable-extreme
authorization, model/effort remaps, and automatic `create_thread` fallback are
not accepted.
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

The 2026-08-14 fresh-CLI proof selected `expected_model=gpt-5.6-luna`,
`model_resolution=configured_default`, and `reasoning_effort=high`, with no
`model` field. The persisted rollout likewise records a native `spawn_agent`
call containing `task_name=explorer` and `reasoning_effort=high` but no `model`
key. Its child snapshot records `model=gpt-5.6-luna` and
`reasoning_effort=high`. This runtime metadata, rather than worker self-report,
is the acceptance evidence. The worker returned to the parent and no
user-owned visible task was created.
This proof predates the current 4.0.4 candidate and is not a fresh-install or
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
The tracked-release command has not been reported for 4.0.4. Historical
candidate results do not validate the current breaking package contract; this
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

The installer performs the configured global Codex config-path safety preflight
before any Codex CLI requirement: an existing config must be a regular,
non-symlink file, otherwise the operation fails closed. This ordering also
protects hosts where the Codex CLI is unavailable.

<!-- GENERATED:END -->
