# Live-dev Git-history comparison

## Scope and conclusion

This note compares the last implementation whose operator workflow was
explicitly documented as working with the current live-dev worktree. It does
not change the stable installed Cortex profile, run a live session, or treat a
unit test as proof that Codex actually rendered a TUI.

The strongest historical baseline is commit `c3eb63f87610665ccdf8cb680654810a216c198c`
(`Update project implementation across 40 files`, 2026-08-28). Its `AGENTS.md`
specified a direct, two-stage tmux interaction: create a background session
whose initial process is `bash`, then inject the complete launcher command with
one `tmux send-keys ... C-m`. The later commit
`032af8fcfd42a09ad3c62fc9435574427086e00a` introduced the repository helper
`scripts/cortex-live-smoke`, but made the launcher itself the initial
`bash -c` process and removed the explicit operator-controlled launch key.

The current worktree has an additional uncommitted bootstrap/capture design:
the pane starts `bash -c 'IFS= read -r _; exec bash -c ...'`, then
`cortex-live-smoke start` attaches a pipe and releases that shell with a
separate `C-m`. This is materially different from the historical working
sequence and adds an extra input gate before `cortex-dev` exists in the pane.
The current `send` path is not the historical failure: it still performs
literal insertion, one initial `C-m`, a five-second drain, and one final
standalone `C-m`.

## Evidence from Git

### Historical sequence: `c3eb63f`

At `c3eb63f:AGENTS.md:55-84`, the documented sequence was:

```bash
session_name=cortex-v12-smoke
tmux_cmd=(tmux -f /dev/null)
"${tmux_cmd[@]}" has-session -t "=$session_name" 2>/dev/null && \
  "${tmux_cmd[@]}" kill-session -t "=$session_name" || true
"${tmux_cmd[@]}" new-session -d -s "$session_name" -c "<repository-root>" bash
"${tmux_cmd[@]}" send-keys -t "=$session_name:0.0" \
  'cd <repository-root> && ./scripts/cortex-dev; status=$?; printf "Cortex live-dev exit=%s\\n" "$status"' C-m
```

The same document explicitly required the workload to be sent later, after
the operator/LLM had inspected the interactive prompt (`:75-82`), and used
`capture-pane` only for bounded observation (`:82`). The fallback at `:86-113`
started output-only `pipe-pane` before the launcher was sent; it never used the
pipe as Codex input or as an automated readiness decision.

The historical `scripts/cortex-dev` at the same commit performed the isolated
environment transition directly: it captured the repository directory,
exported `HOME=<owner>/.cortex-dev` and
`CODEX_HOME=$HOME/.codex`, synchronized from the repository, and ended with
`exec codex "$@"`. There was no intermediate `read` gate and no nested tmux.
The repository-root `cortex-dev` was a one-purpose forwarding wrapper in that
lineage (`c3eb63f:cortex-dev`).

### Helper introduction: `032af8f`

Commit `032af8fcfd42a09ad3c62fc9435574427086e00a` added
`scripts/cortex-live-smoke` and changed the tested launch shape. Its committed
implementation at `032af8f:scripts/cortex-live-smoke:35-55` called:

```text
tmux new-session -d -s cortex-v12-smoke -c <workdir> bash -c \
  "cd <workdir> && <repository>/scripts/cortex-dev; status=$?; printf ...; exit $status"
```

That means the launcher was already the pane's initial process; the helper did
not follow the earlier `new-session ... bash` then `send-keys launcher C-m`
sequence. The committed helper's `send` implementation at
`032af8f:scripts/cortex-live-smoke:65-90` did preserve the working prompt
submission shape: literal `send-keys -l`, one `C-m`, a five-second sleep, and a
second standalone `C-m`.

The commit's own test encoded this new contract. At
`032af8f:tests/test_live_dev_smoke_docs.py:215-240`,
`test_start_launches_cortex_dev_as_the_pane_process` asserted that
`new-session ... bash -c <launcher>` was the only start operation and asserted
that no `send-keys` call occurred during `start`. That test proves the helper's
intended call construction, not successful Codex TUI startup.

### Current uncommitted worktree

The current `scripts/cortex-live-smoke` differs from both baselines:

- `:178-180` builds a quoted launcher command with an event-journal
  environment variable.
- `:204-208` creates an inert shell whose first command is
  `IFS= read -r _; exec bash -c ...`.
- `:212-217` attaches the output observer and then sends a release `C-m`.
- `:235-259` retains the historical prompt insertion/Enter timing.

The current `scripts/cortex-dev` still performs candidate refresh and
provenance verification before `exec codex` (`:47-88`), and restores the
caller cwd before that `exec` (`:7`, `:87`). Those are useful isolation
properties, but they are not part of the historical tmux launch handshake.

## Exact behavioral comparison

| Concern | Historical `c3eb63f` | Committed helper `032af8f` | Current worktree |
| --- | --- | --- | --- |
| Pane initial process | `bash` | `bash -c` running launcher | `bash -c` blocked in `read` |
| Who starts Codex | Later explicit `send-keys` command + `C-m` | tmux starts launcher directly | Later release `C-m` unblocks shell, then `exec bash -c` starts launcher |
| tmux server | Default server with `tmux -f /dev/null` | Same | Same |
| Codex mode | Ordinary interactive `codex` via `exec codex` | Ordinary interactive `codex` via `exec codex` | Ordinary interactive `codex` via `exec codex` |
| CWD | Repository root in launcher | Selected workdir, then launcher | Selected workdir, temporary repository refresh, restored workdir |
| Prompt delivery | Direct operator `send-keys ... C-m` | Helper: literal text, `C-m`, 5 s, `C-m` | Same helper sequence |
| Observation | `capture-pane`; optional output-only pipe | `capture-pane` | Output-only pipe plus bounded event journal |
| Extra launch handshake | None | None | `read` gate + release `C-m` |

## Root cause and recommended correction

The Git evidence does not show a historical regression in the literal prompt
submission sequence. It shows a launch-handshake divergence. The known-good
operator model kept the pane at an ordinary shell prompt and sent the complete
`cortex-dev` command as a later, explicit tmux input. The current helper inserts
an extra shell `read` gate and uses a release key before the Codex process
exists. That key is easy to lose, misdirect, or observe as an unexplained blank
pane when the session is initialized or attached. It also makes `start`'s
success depend on a synthetic shell protocol that never existed in the working
baseline.

## Implemented correction

The minimal correction has now been implemented in the current worktree while
retaining the independently valuable isolation/provenance and output-only
observation features:

1. `start` creates `cortex-v12-smoke` with `new-session -d ... bash`.
2. It attaches the output-only observer before any launcher input.
3. It sends the fully quoted `cd <repository> && <absolute>/scripts/cortex-dev; ...`
   command as literal text to the exact pane, then sends one standalone `C-m`.
4. It returns control to the operator/LLM, which observes the real attached or
   bounded captured terminal and decides when the composer is rendered.
5. `send` remains the separate literal insertion → initial `C-m` → five-second
   drain → final standalone `C-m` sequence.

This correction does not remove orchestration functionality, candidate
provenance, MCP event observation, or worker verification. It changes only the
tmux-to-shell launch transport back to the exact historical interaction that
was documented and manually inspectable. The launch-contract test was updated
from the removed initial `bash -c` expectation to assert the restored sequence:
ordinary `bash`, output observer, literal launcher insertion, standalone
`C-m`. A real attached tmux run is still required before declaring the
correction live-verified.

Implemented files and focused checks:

- `scripts/cortex-live-smoke`: restored the two-step launch topology while
  preserving event capture, selected workdir, exact-session cleanup, and
  transport-only behavior.
- `tests/test_live_dev_smoke_docs.py`: updated the launch-sequence assertion;
  all 23 focused tests pass.
- `AGENTS.md`, `README.md`, `docs/project/verification.md`, and
  `docs/release-readiness.md`: document the ordinary-shell → observer → literal
  launcher → Enter sequence.
- `python3 -m py_compile scripts/cortex-live-smoke` passed.
- `bash -n scripts/cortex-dev cortex-dev` passed.

Candidate refresh and real live-dev execution were intentionally not run in
this correction phase; those are release/live gates for the parent task.

## Boundaries

No conclusion here is based on the stable installed plugin or on private user
logs. Git history establishes the command topology and timing contract; it does
not, by itself, prove that a particular current candidate was byte-identical to
that source. Candidate provenance and the real attached-session live gate must
remain separate release checks.
