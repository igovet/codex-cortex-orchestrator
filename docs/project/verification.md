# Verification

The control plane is validated with the standard-library test suite:

```bash
python3 -m unittest discover -s tests -v
python3 scripts/cortex-cold-boot-smoke.py
python3 scripts/cortex-composite-benchmark.py --workers 8 --waves 5
python3 scripts/probe-fresh-cortex-plugin.py
python3 scripts/verify-cortex-release.py --require-tracked
python3 -m py_compile plugins/cortex/scripts/cortex.py plugins/cortex/scripts/cortex_hook.py scripts/cortex-cold-boot-smoke.py scripts/probe-fresh-cortex-plugin.py scripts/validate-cortex-marketplace.py scripts/verify-cortex-release.py tests/jsonrpc_harness.py
bash -n scripts/sync-cortex.sh
./scripts/sync-cortex.sh --check
./scripts/sync-cortex.sh --dry-run
```

`cortex-cold-boot-smoke.py` is the v2 cold-boot smoke. It creates a fresh
temporary Git project, drives the stdio JSON-RPC server solely through the
public `orchestrate` tool, restarts the server mid-lifecycle, and proves the
one-start/one-advance-per-wave contract. It covers durable identical-request
replay, strict eight-field `cortex/report/v1` reports, actual host completion
fields, server-observed close evidence, report and manifest reconciliation,
documentation decision, handoff, audit, and final completion. Focused unit
regressions cover changed-payload conflicts, validation before writes,
future-wave replacement/rework protection, every transaction checkpoint,
multi-root isolation, and expected `ok: false` results that do not enter the
exception log.

`cortex-composite-benchmark.py` is a call-count contract benchmark, not a
latency benchmark. It reports `legacy_mcp_calls` against
`orchestrate_mcp_calls` and checks the façade target of `waves + 1` calls: one
`start` plus one `advance` per wave. Native host spawns are intentionally
excluded because they remain host calls rather than MCP façade calls. Run it
when changing the public lifecycle; it is not correctness or performance
evidence.

Facade validation regressions cover malformed requests and completions,
including missing/relative roots, malformed nested start waves, invalid
reports, and actual-host-model mismatches. They assert one recoverable
structured `ok: false` result containing all independent preflight
diagnostics, no partial attempt transition after preflight failure, and a
redacted entry in `~/.codex/logs/cortex-tool-errors.jsonl` for every facade
`ok: false` result, including an expected coordinator correction.

`probe-fresh-cortex-plugin.py` copies the full root-layout checkout into a
temporary directory, uses temporary `HOME` and `CODEX_HOME`, installs it with fresh Codex CLI
processes, and checks that `cortex` is exposed. It reports
`SKIP` when the Codex CLI is unavailable; treat that as an environment
limitation, not plugin-registration evidence.

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

The JSON-RPC regression also sends invalid tool requests through a subprocess
and verifies that `~/.codex/logs/cortex-tool-errors.jsonl` records every
redacted facade `ok: false` result as well as MCP exceptions. Records retain
the chat/thread session id, request id, and task/attempt ids with restrictive
file permissions.

Pipeline classification regressions prove that the bounded compatibility aliases
`planning`, `discovery`, and `verification` normalize to the canonical `plan`,
`discover`, and `qa` gate IDs in both the pipeline and parallel-group waves;
unrecognized gate IDs remain hard validation errors.

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
exactly 10 skills ship, all eight report fields are required, and the retired
`task_formatter`/dedicated-orchestrator profiles are absent.

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

<!-- GENERATED:START -->

## Authoritative command inventory

- `python -m unittest discover -s tests -v` — standard-library regression suite; CI source: [cortex.yml](../../.github/workflows/cortex.yml).
- `python3 scripts/cortex-cold-boot-smoke.py` — black-box JSON-RPC lifecycle smoke test; CI source: [cortex.yml](../../.github/workflows/cortex.yml), implementation: [cortex-cold-boot-smoke.py](../../scripts/cortex-cold-boot-smoke.py).
- `python3 scripts/cortex-composite-benchmark.py` — v2 MCP call-count contract benchmark (`legacy_mcp_calls` versus `orchestrate_mcp_calls`); implementation: [cortex-composite-benchmark.py](../../scripts/cortex-composite-benchmark.py). It asserts the `waves + 1` façade target and makes no latency claim.
- `python3 scripts/probe-fresh-cortex-plugin.py` — isolated fresh-plugin registration probe; CI source: [cortex.yml](../../.github/workflows/cortex.yml), implementation: [probe-fresh-cortex-plugin.py](../../scripts/probe-fresh-cortex-plugin.py). `SKIP` means the Codex CLI is unavailable.
- `python3 scripts/verify-cortex-release.py --require-tracked` — blocking tracked-release archive boundary; CI source: [cortex.yml](../../.github/workflows/cortex.yml), implementation: [verify-cortex-release.py](../../scripts/verify-cortex-release.py).
- `python scripts/validate-cortex-marketplace.py` — repository marketplace and plugin-contract validation; CI source: [cortex.yml](../../.github/workflows/cortex.yml), implementation: [validate-cortex-marketplace.py](../../scripts/validate-cortex-marketplace.py).
- `python -m py_compile plugins/cortex/scripts/cortex.py plugins/cortex/scripts/cortex_hook.py scripts/cortex-cold-boot-smoke.py scripts/probe-fresh-cortex-plugin.py scripts/validate-cortex-marketplace.py scripts/verify-cortex-release.py tests/jsonrpc_harness.py` — Python syntax compilation for runtime and helper modules; CI source: [cortex.yml](../../.github/workflows/cortex.yml).
- `bash -n scripts/sync-cortex.sh` — shell syntax check; CI source: [cortex.yml](../../.github/workflows/cortex.yml).
- `./scripts/sync-cortex.sh --check` — read-only installed-content/legacy-artifact check; source: [sync-cortex.sh](../../scripts/sync-cortex.sh).
- `./scripts/sync-cortex.sh --dry-run` — no-write report of the planned installation and managed legacy cleanup; source: [sync-cortex.sh](../../scripts/sync-cortex.sh).

<!-- GENERATED:END -->
