# SSH host troubleshooting

Use the host preflight before changing a remote Codex installation. It is
read-only: it does not install packages, write Codex configuration, alter
plugin files, or touch Cortex orchestration state. Run it as the same SSH user
that will start Codex, with the same `HOME`, `CODEX_HOME`, `PATH`, and
`CORTEX_PYTHON`.

```bash
PYTHONDONTWRITEBYTECODE=1 python3 scripts/cortex-host-preflight.py --json
```

When checking an already cached plugin rather than this checkout, pass the
cached plugin directory explicitly:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 scripts/cortex-host-preflight.py \
  --plugin-root "$CODEX_HOME/plugins/cache/cortex/cortex/<version>" --json
```

## READY contract

The readiness contract has seven operational prerequisite checks. The JSON
output emits one record for each check:

- `codex_cli` — the same user resolves `codex` and `codex --version` exits 0.
- `cortex_python` — the selected interpreter is executable, Python 3.11 or
  newer, and imports `tomllib`.
- `plugin_root` — the checked source or cache has a regular manifest, MCP
  route, `scripts/cortex.py`, and executable launcher with consistent content.
- `codex_home` — the same user's cache contains the matching plugin version,
  regular contract files, and content matching the checked plugin root.
- `cortex_registration` — `codex plugin list --json` has exactly one enabled,
  installed `cortex@cortex` entry with the checked version and plugin name.
- `cortex_mcp_config` — the same user's regular `config.toml` enables
  `cortex@cortex` and sets
  `plugins."cortex@cortex".mcp_servers.cortex.default_tools_approval_mode =
  "approve"`. If the user selects a granular approval policy, it also sets
  Cortex questions use ordinary chat and do not require an MCP-elicitation policy.
- `cortex_hook_trust` — `hooks/list` returns exactly five enabled, trusted
  `cortex@cortex` hooks from the matching cache, with valid hashes matching the
  persisted hook-state table.

`mcp.status=READY` is returned only when every emitted check is `PASS` and the
`mcp.blocking_checks` list is empty. A cached directory or a successful
source-mode MCP launch alone is not registration evidence. A `SKIP` from the
isolated fresh-plugin probe likewise means that the Codex CLI was unavailable;
it is not a passing registration result.

| Check | What it proves | Failure consequence |
| --- | --- | --- |
| `codex_cli` | The SSH user can run Codex from its own `PATH`. | Codex cannot load or register the plugin. |
| `cortex_python` | The launcher-selected interpreter meets the Python 3.11+/`tomllib` contract. | The MCP server and lifecycle hooks stop before `cortex.py`. |
| `plugin_root` | The checked plugin source has a trusted manifest, route, launcher, and entrypoint. | The source/cache contents cannot be trusted. |
| `codex_home` | The same-user cache is version- and content-aligned with the checked plugin. | Codex may load stale or incomplete package content. |
| `cortex_registration` | The same user has one enabled matching `cortex@cortex` registration. | MCP registration is not proven for this user. |
| `cortex_mcp_config` | The same-user Cortex MCP table is enabled and approval is `approve`. | Cortex lifecycle tool calls may be blocked before orchestration starts. |
| `cortex_hook_trust` | All five lifecycle hooks are enabled, trusted, cache-backed, and hash-matched. | Worker binding, finalization guard, and stopped-worker recovery are not trusted. |

The script returns exit code `0` only for `READY`; an expected negative result
must be wrapped by a test harness that asserts exit code `1` and itself exits
`0`. It never follows symlinked contract paths and keeps command output
bounded.

The checks are implemented by the [host preflight script](../../scripts/cortex-host-preflight.py)
and covered by its [host-preflight regression suite](../../tests/test_cortex_host_preflight.py).
The preflight is a diagnostic only: it does not install the Codex CLI, change
the same-user configuration, or repair hook trust.

## Investigated host boundary

The named `Hetzner_Bots` SSH probe was read-only. It found no `codex` executable,
the default `python3` was 3.10.12, and the cached launcher could not import
`tomllib`. The configured package source exposed Node 12, while the Codex
package requires Node >=16. No remote package installation or configuration
mutation was performed. Until an approved Node >=16 source is supplied, the
host is externally blocked and must not receive a guessed package-manager
command or an unapproved runtime source.

Keep this remote result separate from local source evidence. The checkout can
pass launcher or source-mode MCP checks while the SSH user's cache is stale or
the SSH runtime is unsupported. In the local evidence recorded for this task,
the source was 6.6.1 while the installed cache was 6.6.0; that mismatch is a
preflight failure, not proof that the source is broken.

## Safe same-user remediation sequence

Remote writes are operator-owned and remain limited to `Hetzner_Bots` and the
same SSH user. First collect read-only evidence:

```bash
command -v codex
python3 --version
python3 -c 'import tomllib; print("tomllib: ok")'
```

If `python3` is older than 3.11, select an already installed supported
interpreter with an absolute path:

```bash
export CORTEX_PYTHON=/absolute/path/to/python3.11
```

Do not choose a package source or installation command without explicit
authority. Once an approved Node >=16 source and the required same-user
installation authority exist, run the repository's operator-owned installer
from a trusted checkout, then its read-only check:

```bash
./scripts/sync-cortex.sh
./scripts/sync-cortex.sh --check
```

Re-run the preflight against the matching cache, preserving the same
environment:

```bash
PYTHONDONTWRITEBYTECODE=1 "$CORTEX_PYTHON" scripts/cortex-host-preflight.py \
  --plugin-root "$CODEX_HOME/plugins/cache/cortex/cortex/<version>" --json
```

If `CORTEX_PYTHON` is intentionally unset because `python3` already meets the
requirement, use `PYTHONDONTWRITEBYTECODE=1 python3` instead. Proceed only when
all seven check records pass and `mcp.status` is `READY`; then start a fresh
Codex thread so its MCP and lifecycle-hook paths are reloaded. A source-mode
`initialize`/`tools/list` smoke proves the checkout, not the installed cache.

## Lifecycle recovery

Do not diagnose an expected lifecycle/domain response by inspecting Cortex
source, installed plugin/cache, logs, database/ledger, session, environment, or
host state. The returned public `error` and `recovery` card is authoritative.
Preserve the exact opaque `task_ref` **and** `coordinator_ref`; Cortex owns and
issues both values, and a missing value fails closed rather than being inferred
from a thread, worker, or project directory.

1. Follow only the returned action and recovery. `same_operation` is permitted
   only if the response or already-held canonical server contract supplies
   explicit `allowed_changes` for one deterministic legal retry.
2. For `terminal_stop`, take no retry, inspect, or continuation action: its
   action is `none`, and perform only explicitly prescribed cleanup.
3. A missing worker bootstrap pair permits exactly one `followup_task` to that
   same native child with the byte-identical server-built repair message, then
   an exact wait. A second missing marker is finalized through the prescribed
   `finalize_bootstrap_failure` operation; never spawn a replacement.
4. On a durable question, retain its exact `question_ref`. A scalar answer or
   stable-option selection resumes the same child, whose first worker call is
   the exact scalar `worker_question(action:"poll", task_ref, assignment_ref,
   question_ref)` form. Do not remove and recreate a question for a ref
   mismatch.
5. An exact `CORTEX_ATTEMPT_FAILED retryable=false` is status only. Use the
   prescribed `finalize_worker_failure` cleanup only after structured
   `recovery.terminal_failure.evidence="server_bound"`; missing, stale,
   wrong-dispatch, or replayed server evidence rejects without mutation.

## Verification

Run focused local checks without writing repository bytecode:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v tests.test_cortex_host_preflight
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v \
  tests.test_attempt_facade_lifecycle \
  tests.test_worker_context_recovery \
  tests.test_v11_install_gate \
  tests.test_cortex_invariants
python3 scripts/validate-cortex-marketplace.py
git diff --check
```

The first command validates aligned readiness, each registration/configuration
failure, hook trust, stale caches, and symlink boundaries. The lifecycle suites
cover explicit capabilities, persisted results, durable questions, terminal
worker stops, bounded corrective dispatch, and effort/model escalation. These
local checks do not prove that the named remote
host has been provisioned; that remains blocked until the approved Node >=16
source and same-user authority are available.
