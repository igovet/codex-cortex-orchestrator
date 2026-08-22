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
- `cortex_hook_trust` — `hooks/list` returns exactly six enabled, trusted
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
| `cortex_hook_trust` | All six lifecycle hooks are enabled, trusted, cache-backed, and hash-matched. | Worker binding, finalization guard, and stopped-worker recovery are not trusted. |

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

## Stuck-ledger recovery

The formerly stuck state is an active gate with no pending dispatches and a
stopped native worker. Recovery is bounded and identity-scoped:

1. Preserve the opaque `task_ref` and call
   `manage_orchestration(intent="inspect")` once. Do not restart the task or
   call `start_orchestration` again.
2. From `context_handoff`, invoke only returned `pending_dispatches`; wait only
   on the exact child IDs in `active_workers`. The handoff itself never
   authorizes a spawn.
3. If a stopped worker has `attempt_result_ref` values, read those results and continue the
   current step. If it has a durable question, surface the question and resume
   the same persisted worker only after the answer.
4. If it has no AttemptResult or question, it is terminal failed with
   `failure_reason="native_worker_stopped_without_attempt_result"`. Never wait on,
   respawn, or call `followup_task` for that stopped child. Submit exactly one
   non-success result to `continue_orchestration` using the current `step`, the
   exact `dispatch_ref`, `status="failed"`, and that reason:

```json
{
  "status": "failed",
  "reason": "native_worker_stopped_without_attempt_result",
  "dispatch_ref": "<exact-dispatch-ref-from-inspect>"
}
```

   Cortex alone may then return one fresh top-level dispatch. A duplicate
   submission, a guessed child identity, or an empty wait is not a recovery
   action.

Cortex permits unbounded automatic corrective attempts while acceptance or a
canonical finding still requires work. After the first prior failure the
effort floor is `high`, after the second it is `xhigh`, and after the third and
later failures it is `max`; eligible ordinary work moves to Terra after two
prior failures. A supplied `next_strategy` is useful audit evidence but is not
required to continue, and an unchanged strategy never blocks rework. Only an
explicit non-retryable integrity, storage, permission, identity, or environment
blocker—or user cancellation—halts the task. AttemptResult-backed stops and
durable-question stops remain separate paths: neither is a reason to follow up
a dead child.

The runtime contract is implemented by the stop finalizer in
[`cortex.py`](../../plugins/cortex/scripts/cortex.py), the recovery handoff in
[`context_handoff.py`](../../plugins/cortex/scripts/cortex_runtime/context_handoff.py),
and the lifecycle hook in [`cortex_hook.py`](../../plugins/cortex/scripts/cortex_hook.py).
The focused control and revision regressions cover AttemptResult consumption,
durable-question resumption, terminal worker stops, bounded corrective
dispatch, and effort/model escalation.

## Verification

Run focused local checks without writing repository bytecode:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v tests.test_cortex_host_preflight
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v \
  tests.test_cortex_control \
  tests.test_revision_aware_epic \
  tests.test_cortex_invariants
python3 scripts/validate-cortex-marketplace.py
git diff --check
```

The first command validates aligned readiness, each registration/configuration
failure, hook trust, stale caches, and symlink boundaries. The recovery suites
cover persisted results, durable questions, terminal worker stops,
unbounded corrective dispatch, and effort/model escalation. These local checks do not prove that the named remote
host has been provisioned; that remains blocked until the approved Node >=16
source and same-user authority are available.
