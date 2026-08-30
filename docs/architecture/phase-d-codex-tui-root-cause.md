# Phase D Codex TUI launch root cause

Status: **transport correction implemented; live decision acceptance remains
unrun until an operator/LLM observes the real session and performs the
task-specific decision scenario.** This document contains sanitized evidence only. It contains no
prompts, credentials, opaque task identifiers, or raw private logs.

## Finding

The latest no-composer observation was not caused by Cortex MCP, candidate
provenance, the Python launcher, or a missing PTY. A fresh test-project
directory was not trusted in the isolated Codex profile. Codex therefore
started its ordinary interactive TUI and stopped at its directory-trust
confirmation before it could expose the composer. The confirmation was not
visible through the normal bounded `tmux capture-pane` result, so the prior
observation incorrectly appeared to be a silent/no-output launch.

Two independent observations establish the cause:

1. A direct ordinary Codex launch and a `scripts/cortex-dev` launch both
   received real `/dev/pts/*` descriptors for stdin, stdout, and stderr, and
   both remained live in the named pane. This rules out the launcher losing the
   PTY or replacing Codex with a non-interactive process.
2. An output-only `tmux pipe-pane` capture of the same ordinary process showed
   the Codex trust screen and its `Press enter to continue` gate for the fresh
   directory. After the exact directory was marked trusted only in the
   isolated candidate profile, the same launch rendered the normal composer.
   No workload text or workload Enter was sent during either diagnostic.

The currently supported `capture-pane` command is not a sufficient readiness
source for this Codex TUI when the pane has no attached terminal client: it
continued to show the shell/launcher screen even while the raw PTY stream
contained the rendered Codex trust screen and composer. A short real tmux
client attachment rendered the same composer immediately. Thus there are two
separate gates: directory trust before composer readiness, and output evidence
that can lose a TUI redraw when relying on detached `capture-pane` alone.

## Reproduction and discrimination

The diagnosis used only the current user's default tmux server and the exact
named session `cortex-v12-smoke`; it never used `codex exec`, a nested server,
an alternate socket, or the stable profile.

The bounded sequence was:

```text
./scripts/cortex-live-smoke start --workdir <fresh-owner-only-test-directory>
./scripts/cortex-live-smoke status
tmux -f /dev/null capture-pane -p -t =cortex-v12-smoke:0.0 -S -200 -E -1
```

The launcher reached all candidate gates and printed verified source/candidate
provenance. The process tree then showed `bash -> codex -> cortex MCP
server`, with all three standard descriptors attached to the pane PTY. The
normal pane capture still contained only the launcher output.

For diagnosis, an output-only pipe was enabled before a direct ordinary Codex
launch in the same pane. Its bounded, sanitized semantic markers were:

```text
OpenAI Codex (v0.149.1)
You are in <fresh-test-directory>
Do you trust the contents of this directory?
1. Yes, continue
2. No, quit
Press enter to continue
```

No product prompt was inserted. The only isolated change in the confirming
run was a project trust entry in the isolated profile. The output stream then
contained:

```text
OpenAI Codex (v0.149.1)
model: gpt-5.6-luna high
directory: <fresh-test-directory>
Ask Codex to do anything
```

The same result was obtained after starting through `scripts/cortex-dev` with
the candidate refresh and provenance checks enabled. This proves the
candidate/launcher path can reach a usable interactive composer once the
environment prerequisite is satisfied.

## Historical comparison

The last repository revisions before the current live helper are:

| Revision | Relevant behavior |
| --- | --- |
| `88cbd3f` | Documented the original ordinary interactive sequence: create a tmux session and run `codex`. |
| `f929bdd` | Added the isolated `scripts/cortex-dev` contract; it still ended in `exec codex`. |
| `c3eb63f` | Added the repository-root forwarding wrapper; its isolated script still ended in `exec codex`. |
| `032af8f` | Added `scripts/cortex-live-smoke`; the helper creates a detached named tmux session and injects the launcher into it. The current launcher additionally restores the caller cwd and performs candidate receipt/provenance checks before the same `exec codex`. |

The historical isolated profile contains a successful Codex TUI startup event
at 2026-08-28 22:27 UTC, before the `032af8f` live-helper addition. That
startup followed the normal Codex initialization path and is consistent with
an already trusted project. No historical source revision contains a
different PTY-preserving Codex launch primitive that would explain the current
failure; the relevant scripts all use an ordinary interactive `codex` process.

Therefore blindly reverting content-addressed delivery, candidate receipts,
or the current isolated launcher would not address this incident. The
behavioral difference is the fresh untrusted project plus detached-TUI
observation, not the Cortex runtime payload.

## Required forward correction

The live-dev contract must treat directory trust as an explicit environment
precondition and must expose the actual TUI stream to the coordinator/LLM:

- Before the first workload prompt, the operator/coordinator must inspect the
  real attached or output-only bounded stream. If Codex presents the
  directory-trust gate, it must be acknowledged once as an environment
  prerequisite; this is not a product clarification, plan approval, or
  acceptance decision. No workload text may be sent before the composer is
  visibly rendered.
- A task-specific test project may surface the trust gate for one explicit
  operator action. The stable profile must never be changed, the transport
  must not silently trust an arbitrary path, and it must not edit Codex trust
  configuration. The operator/LLM may acknowledge only a visibly observed
  gate once using the separate single-Enter transport action.
- The transport must retain its current dumb role: it may start/stop the
  exact named session, deliver literal text/keys, and expose bounded
  observations. It must not parse acceptance, answer the product question,
  approve a plan, or decide pass/fail.
- `capture-pane` alone cannot be called a readiness proof for this TUI. Use an
  attached real client or an output-only `pipe-pane` fallback when the bounded
  pane does not contain the rendered composer. Pipe capture must be owner-only,
  bounded, stopped before cleanup, and sanitized before it is reported.
- Candidate provenance remains a separate mandatory gate. A composer after a
  trust acknowledgement proves only interactive startup; it does not prove
  any Cortex MCP operation.

## Acceptance tests

The following tests are required before promoting the focused live gate:

| Test | Acceptance condition |
| --- | --- |
| Fresh trusted test project | Start through `cortex-live-smoke` and `cortex-dev`; verified candidate lines and a real composer are observable without stable-profile writes. |
| Fresh untrusted test project | The bounded observation identifies the trust gate; no workload prompt is sent early; one explicit environment acknowledgement then reveals the composer. |
| Detached capture loss | When `capture-pane` omits the TUI, the output-only bounded stream or real attachment contains the same trust/composer state; the driver reports unverified if neither is available. |
| Launcher parity | Direct ordinary Codex and `cortex-dev` have the same PTY descriptors, cwd, terminal dimensions, and interactive startup behavior. |
| Workload gate | Only after the composer is visibly confirmed does the coordinator deliver one task-specific prompt using the existing literal-insert plus standalone-Enter transport. |
| MCP gate | After startup, the focused decision scenario independently proves first-call schema validity, no MCP tool error, no unexplained mutation replay, and complete worker-event inspection. |

Until these tests pass, the Phase D result is **failed/unverified**, not a
live acceptance pass. The diagnostic session was stopped by targeting only
`cortex-v12-smoke`; the default tmux server and stable profile were preserved.

## Implemented forward transport — 2026-08-29

`scripts/cortex-live-smoke start` now creates its exact default-server session
with a short inert bootstrap, creates an owner-only temporary capture directory
and bounded file, attaches `tmux pipe-pane` to `=cortex-v12-smoke:0.0`, and
only then releases the fixed ordinary `cortex-dev` launcher. The pipe is
strictly output-only: it does not parse terminal content or provide input to
Codex. `capture` reads the bounded terminal stream after display-safe control
sequence removal, so a trust/composer alternate-screen redraw remains
observable when normal detached pane capture is stale.

The distinct `enter` action sends exactly one standalone Enter to the same
exact pane. It makes no trust, readiness, prompt, rollout, acceptance, error,
approval, retry, or MCP decision. It exists solely for the operator/LLM after
the real terminal has visibly shown the fresh-project acknowledgement screen.
It never modifies the stable profile or Codex trust configuration.

`stop` first stops the exact pane's pipe, then removes only the named session
and its validated owner-only temporary capture state. The raw short-lived
stream is never retained as architecture evidence; any result record is a
bounded sanitized observation authored by the LLM. Unit coverage proves pipe
ordering, bounded tail retention, private permissions, alternate-screen
visibility, exact single-Enter targeting, stale/missing-session behavior, and
cleanup without default-server or main-profile effects. No live session was
started while implementing this transport correction.
