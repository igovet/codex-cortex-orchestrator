# Phase D focused live-dev verification

Status: **blocked before Codex readiness**. The isolated marketplace
reconciliation now succeeds, but the launcher provenance check still rejects
the freshly installed stamped candidate. This recorded retry did not exercise
the decision vertical slice and is not live acceptance evidence.

## Attempt — 2026-08-29

The verifier used the repository-supported transport and the current user's
default tmux server:

```text
./scripts/cortex-live-smoke start --workdir /tmp/cortex-v12-live-project-phase-d
./scripts/cortex-live-smoke status
tmux -f /dev/null capture-pane -p -t =cortex-v12-smoke:0.0 -S -200 -E -1
```

The work directory was a fresh, owner-only temporary directory. The named
session was created successfully, but the launcher exited before the
interactive Codex composer appeared. The bounded output showed candidate
staging and validation, followed by this sanitized failure:

```text
Cortex isolated candidate target: HOME=<isolated-dev-home> CODEX_HOME=<isolated-dev-codex-home>
Cortex uniform tool catalog and model-routing table are current
marketplace validation passed: repository marketplace manifest
release validation passed: working-tree source candidate
staged Cortex candidate: 1.12.1+codex.sha256.<content-addressed-id>
Error: marketplace 'cortex' is already added from a different source; remove it before adding this source
Cortex live-dev exit=1
```

The exact `cortex-v12-smoke` session was removed after the failure. The
default tmux server was not killed. No prompt was inserted, no Enter key was
sent, no MCP tool was called, and no worker was spawned. Therefore there is
no decision-operation, clarification, report, replay, or hidden-worker
evidence from this attempt.

## Gate result

| Gate | Result |
| --- | --- |
| Isolated candidate refresh | Reached staging, then failed in marketplace registration |
| Candidate provenance lines | Not reached in pane before launcher exit |
| Interactive Codex composer | Not observed |
| Task-specific prompt | Not sent |
| Clarification open/record | Not run |
| Worker structured events | Not applicable |
| Cortex tool/validation errors | Not reached |
| Live outcome | Failed/unverified; no pass claimed |
| Cleanup | Exact named session removed; tmux server preserved |

## Delivery remediation implemented before retry

The isolated marketplace lifecycle is now convergent in the supported launcher
path. It checks the exact non-symlinked isolated target before it uses the
native Codex CLI to list the marketplace. A matching candidate source is
reused; a missing source is added; a differently sourced `cortex` entry alone
is removed and replaced. The stable profile and unrelated isolated
marketplaces are outside the operation. Regression coverage proves stale,
missing, same-source, unrelated-entry, symlink, and main-profile cases.

This is not retroactive live evidence. Rerun this same focused gate from a
fresh test project through the wrapper. Do not bypass it with direct sync,
`codex exec`, a foreground console, an alternate tmux socket, or manual stable
profile changes.

Phase H gates remain unrun, including the complete multi-turn clarification →
plan approval → planner → implementation → independent verification →
documentation-impact → closure scenario and the requirement for three
consecutive clean live E2Es.

## Event-observation delivery boundary

The live helper now provisions a fresh owner-only sanitized MCP observation
journal for the exact isolated session and project before releasing the normal
Codex launcher. The LLM uses `cortex-live-smoke events` together with the
visible pane: it must independently decide whether the stream shows a hidden
tool error, validation error, unexplained replay, and the first successful
worker-owned publication. The helper does not parse those conditions or decide
pass/fail. This source-level transport correction is not retroactive live
evidence; no live pass is claimed until the focused scenario is rerun.

## Retry — 2026-08-29 (post-restart)

After the host restart, the verifier used a fresh owner-only temporary project
and the repository-supported transport on the current user's default tmux
server:

```text
./scripts/cortex-live-smoke start --workdir /tmp/cortex-phase-d-live-retry.<unique>
./scripts/cortex-live-smoke status
./scripts/cortex-live-smoke capture --lines 200
```

The exact named session was created. Bounded pane capture showed successful
isolated marketplace validation, release validation, content-addressed
candidate staging, and installation from the current repository. The launcher
then failed its own pre-Codex provenance check:

```text
Cortex isolated candidate target: HOME=<isolated-dev-home> CODEX_HOME=<isolated-dev-codex-home>
Cortex uniform tool catalog and model-routing table are current
marketplace validation passed: repository marketplace manifest
release validation passed: working-tree source candidate; files=94
staged Cortex candidate: 1.12.1+codex.sha256.<content-addressed-id>
isolated Cortex marketplace source is current
configured Cortex MCP default_tools_approval_mode=approve
installed cortex@cortex from <isolated-candidate-staging-root>
candidate cache is missing for 1.12.1: <isolated-CODEX_HOME>/plugins/cache/cortex/cortex/1.12.1
error: isolated Cortex candidate provenance verification failed; Codex will not start
Cortex live-dev exit=1
```

This is a delivery/provenance defect, not a decision-tool result. The sync
path installs the stamped content-addressed candidate
`1.12.1+codex.sha256.<id>`, while `scripts/cortex-dev` searches only for a
base-version cache directory named `1.12.1`. Consequently the ordinary Codex
process and interactive composer never start. No task-specific prompt was
created or sent, no Enter key was injected, no MCP operation was called, and
no worker event stream exists for this retry. The exact named session had
already exited after the launcher failure; the default tmux server was not
killed and no stable profile was touched.

## Retry gate result

| Gate | Result |
| --- | --- |
| Isolated candidate refresh | Passed through content-addressed staging and isolated marketplace installation |
| Candidate provenance lines | Blocked: launcher searched for unstamped `1.12.1` cache path |
| Interactive Codex composer | Not observed |
| Task-specific semantic prompt | Not sent |
| Clarification open/record | Not run |
| Worker structured events | Not applicable |
| Cortex tool/validation errors | Not reached |
| Live outcome | Failed/unverified; no pass claimed |
| Cleanup | The exact session exited; default tmux server and stable profile preserved |

The next live attempt is forbidden until the launcher and candidate builder
share one authoritative content-addressed identity (or the launcher resolves
the exact stamped installed path from server state), then the source and
candidate provenance checks pass before Codex launch. Do not work around this
by creating a base-version symlink/cache alias, running direct sync, using
`codex exec`, selecting another tmux socket, or changing the stable profile.

## Delivery correction pending live retry — 2026-08-29

The failed retry identified a concrete delivery split: sync installed the
stamped candidate, while the launcher independently reconstructed an unstamped
`1.12.1` cache directory. That reconstruction has been removed. The supported
isolated sync now commits an owner-only authoritative receipt after native
installation and exact parity verification; the launcher validates and consumes
the receipt's exact stamped path, version, build ID, digests, and isolation
identity before it may execute Codex. It fails closed for missing, stale,
tampered, cross-isolated, symlinked, or parity-mismatched receipt state.

Focused isolated regressions are green for the delivery boundary, including a
native-install success followed by receipt-write failure. This does **not**
change the outcome above into a live pass: no real `cortex-live-smoke` session
has been started after this correction, no composer has been observed, and no
MCP decision operation or worker event has been exercised. The next action is
one fresh operator-controlled focused live retry using the exact named default
tmux session and the required LLM observation policy.

## Retry after authoritative receipt fix — 2026-08-29

The receipt correction was exercised from a fresh owner-only temporary project
through the supported operator transport:

```text
./scripts/cortex-live-smoke start --workdir /tmp/cortex-phase-d-live-fixed.<unique>
./scripts/cortex-live-smoke status
./scripts/cortex-live-smoke capture --lines 120
```

The exact `cortex-v12-smoke` session was visible on the current default tmux
server. Bounded capture confirmed isolated marketplace reconciliation,
content-addressed staging, receipt verification, and exact source/candidate
parity before the Codex process was started:

```text
marketplace validation passed: repository marketplace manifest
release validation passed: working-tree source candidate; files=95
staged Cortex candidate: 1.12.1+codex.sha256.<content-addressed-id>
isolated Cortex marketplace source is current
configured Cortex MCP default_tools_approval_mode=approve
verified isolated Cortex candidate receipt
Cortex server version=1.12.1
Cortex candidate version=1.12.1+codex.sha256.<content-addressed-id>
Cortex source/candidate parity=verified
Cortex candidate path=<exact-stamped-installed-candidate>
```

The ordinary `codex` process remained attached to the pane, but no interactive
composer or other Codex TUI output rendered during more than one minute of
bounded observation. A second start removed inherited desktop/CI thread
markers before invoking the same helper; it produced the same no-composer
result. A live process and `pane_current_command` were not treated as
readiness. Because the composer was never visibly confirmed, no task prompt
was created or sent and no Enter key was injected. No Cortex MCP operation,
clarification, worker event, report, replay, or tool error was observed.

The exact session was terminated with
`./scripts/cortex-live-smoke stop --interrupt`; the tmux server and stable
profile were preserved. This retry is **failed/unverified**, not live
acceptance evidence. Candidate receipt/provenance is now a passing gate; the
remaining blocker is the ordinary isolated Codex interactive launch. The next
source investigation must explain the absent PTY/TUI output (including fresh
project trust and isolated host-session state) before any prompt delivery.

## TUI diagnosis — 2026-08-29

The startup blocker is now identified and recorded in the [Codex TUI root-cause
report](phase-d-codex-tui-root-cause.md). A fresh test project was untrusted
in the isolated profile, so ordinary Codex stopped at its directory-trust
confirmation before workload readiness. An output-only `pipe-pane` capture
showed that trust screen and, after an isolated trust entry was added, the
same `cortex-dev` launch showed the normal composer. The launch retained real
PTY descriptors throughout.

The normal bounded `capture-pane` path did not expose the active Codex TUI
screen while the detached pane was running; a real tmux client attachment and
the output-only stream did. This is an evidence-transport defect to address in
the live-dev contract, not evidence of a successful MCP run. No workload
prompt, product clarification, plan approval, or Enter submission was sent.

The root cause is therefore **diagnosed**. The transport correction now exposes
the owner-only bounded output-only stream and adds an explicit one-Enter action
for a visibly observed fresh-project trust screen; it does not automatically
trust a directory, parse the TUI, or decide acceptance. The focused live gate
remains **unrun** until an operator/LLM observes that real session, confirms the
composer after any explicit trust acknowledgement, and completes the
task-specific decision smoke. No MCP or worker acceptance claim is made from
this transport correction.

## Transport-only retry precondition — 2026-08-29

Use `./scripts/cortex-live-smoke start --workdir <existing-test-project>`, then
observe `status`, `capture`, or an attached tmux client. If and only if the
visible stream presents the Codex fresh-project acknowledgement, the
operator/LLM may issue `./scripts/cortex-live-smoke enter` once and must again
observe the composer before sending a workload prompt. The helper never makes
that observation or decision itself. Its pipe is owner-only and bounded;
`stop` closes it before exact-session cleanup. This is a transport prerequisite,
not live evidence.

## Retry with transport bootstrap and trust-screen guard — 2026-08-29

The corrected transport was retried from two fresh owner-only temporary
projects. It created the exact `cortex-v12-smoke` session on the current
default tmux server, attached its bounded owner-only output observer before
releasing the launcher, and exposed the session through `status`/`capture`.

Both runs showed the isolated marketplace and candidate receipt gates passing:

```text
marketplace validation passed: repository marketplace manifest
release validation passed: working-tree source candidate; files=95
staged Cortex candidate: 1.12.1+codex.sha256.<content-addressed-id>
isolated Cortex marketplace source is current
configured Cortex MCP default_tools_approval_mode=approve
verified isolated Cortex candidate receipt
Cortex server version=1.12.1
Cortex candidate version=1.12.1+codex.sha256.<content-addressed-id>
Cortex source/candidate parity=verified
Cortex candidate path=<exact-stamped-installed-candidate>
```

No fresh-project trust screen was visible in the bounded pane or output
capture. Consequently the verifier did not invoke `./scripts/cortex-live-smoke
enter`, did not send a task prompt, and did not inject Enter through any other
path. The ordinary `codex` process remained attached to the PTY, but no
interactive composer or TUI output rendered during bounded observation. One
retry inherited desktop/CI thread markers; a second removed those markers at
the transport invocation boundary with the same no-composer result. Neither a
running process nor `pane_current_command=bash` was treated as readiness.

No MCP call, clarification, decision binding, worker event, report, replay, or
Cortex tool/validation/schema/traceback error was observed. Both sessions were
stopped with `./scripts/cortex-live-smoke stop --interrupt`; the default tmux
server and stable profile were preserved. The focused live gate remains
**failed/unverified** at the ordinary Codex composer boundary. The complete
Phase H multi-turn orchestration and three-consecutive-clean-live-E2E gate
remain unrun.

## Real attached-client decision retry — 2026-08-29

After the host restart, a fresh owner-only temporary test project was used with
the repository-supported transport on the current user's default tmux server.
The verifier started the exact `cortex-v12-smoke` session, attached a real
interactive tmux client through a TTY, and kept that client attached while
polling its bounded output. The launcher completed candidate refresh and
provenance checks before Codex launch:

```text
candidate version: 1.12.1+codex.sha256.<content-addressed-id>
server version: 1.12.1
source/candidate parity: verified
candidate path: <exact isolated stamped candidate>
configured Cortex MCP default_tools_approval_mode=approve
```

The real attached screen first displayed the fresh-project trust prompt. The
verifier sent exactly one explicit Enter through `cortex-live-smoke enter`,
then waited until the interactive composer was visibly rendered. The task
prompt was delivered once through `cortex-live-smoke send --prompt-file`; the
transport inserted one normalized line, sent its initial submit key, waited
five seconds, and sent one standalone submit key. The model visibly asked one
product clarification, and the verifier delivered exactly one answer:
`Use a blue accent color.` No further answer was sent.

Observed successful coordinator calls were one `open_task`, one
`assess_governance`, and one `open_assignment`, each with a successful
structured result. The visible clarification was rendered as ordinary model
text, but the session never produced an `open_clarification` call or a
`record_clarification` call. Therefore the clarification was not proven to be
durably opened or recorded through the family-specific decision boundary.
The coordinator then remained in `Waiting for agents`/`Working` without a
worker-owned report-submission result. No bounded worker event stream could be
accepted as clean evidence.

No `Cortex tool error`, `validation_error`, `schema_unsupported`, traceback,
or unexplained successful mutation replay was observed. This absence does not
turn the run into a pass: the required family-specific decision calls and the
first worker report success were missing. The run is **failed/unverified** at
the live decision gate. It is not evidence for candidate or Phase H
acceptance.

The attached TTY client was terminated only as part of failure cleanup after
the evidence settled; the exact named session was removed with
`./scripts/cortex-live-smoke stop --interrupt`. The helper state and bounded
capture were removed, the default tmux server was preserved, and the stable
profile was not touched. Because the run was interrupted after a failed gate,
no `Cortex live-dev exit=0` marker was observed.

| Real attached-client gate | Result |
| --- | --- |
| Isolated refresh and candidate provenance | Passed |
| Trust acknowledgement and visible composer | Passed; one explicit Enter |
| Task prompt delivery | Passed; one transport submission |
| Genuine clarification rendered | Passed |
| `open_clarification` durable open | **Not observed** |
| `record_clarification` durable record | **Not observed** |
| Worker structured report stream | No accepted first report event |
| MCP/tool/schema/traceback errors | None observed |
| Live decision outcome | **Failed/unverified** |
| Zero-status launcher marker | Not observed after failure interruption |
| Cleanup | Exact session/helper state removed; default tmux preserved |

## Root-cause disposition — 2026-08-29

The failed live decision path is now diagnosed as a **Clarification Hold
architecture gap**, not a tmux/Enter/candidate-provenance failure. The
[root-cause report](phase-d-live-decision-root-cause.md) establishes that the
verified candidate rendered an ordinary coordinator-session question after a
worker assignment but had no durable hold, answer-to-worker delivery relation,
or worker-event capture capability. The answer was therefore ordinary chat, not
queued continuation input for the worker.

No focused live retry is permitted until the Clarification Hold aggregate,
semantic catalogue/renderer cutover, host continuation bridge, and bounded
sanitized assignment-event capture pass their source and exact-candidate gates.
This disposition preserves model-owned questions, DAG adaptation, and worker
selection; it only makes the existing clarification/recovery relationship
durable and observable.

## Focused LLM-driven attempt after Clarification Hold contract update — 2026-08-29

The subsequent operator-controlled live result is recorded in
[Phase D focused live result](phase-d-live-result.md). The isolated candidate,
real tmux attachment, visibly observed trust acknowledgement, composer, and one
literal workload submission succeeded. The coordinator acknowledged the route
but made no observable first Cortex MCP call before bounded failure cleanup;
the follow-up read-only diagnosis is recorded in
[Phase D live first-call root cause](phase-d-live-first-call-root-cause.md).
the exact-session sanitized event stream was empty. Consequently the attempt
did not reach the clarification, worker, publication, or error gates and is
**failed/unverified**. It is neither a candidate pass nor evidence that the
Clarification Hold implementation works live.

## Passive activation receipt retry — 2026-08-29

The first retry after adding the passive activation boundary is
**failed/unverified before task delivery**. A real attached client completed
the fresh-project trust acknowledgement and visibly rendered the composer; the
isolated candidate receipt also showed source/candidate parity. However, the
exact-session sanitized MCP observation stream did not emit the required one
`server_ready` registration record. The LLM therefore did not send a workload
or infer that an ordinary-host startup was healthy. The full sanitized timeline
and affected later stages are in [Phase D focused live result](phase-d-live-result.md).
The subsequent propagation diagnosis is
[Phase D live server-ready root cause](phase-d-live-server-ready-root-cause.md):
the current package does not declare a host-to-plugin environment route, so the
exact-session path exported by the tmux launcher cannot be a live guarantee.

## V19 exact-session lease live retry — 2026-08-29

**Failed/unverified before task delivery.** The v19 isolated candidate passed
the visible refresh/provenance boundary and an attached ordinary Codex client
reached the composer after one observed trust acknowledgement. However, the
exact-session event reader reported that the runtime-owned observation
generation was unavailable. The LLM therefore correctly did not submit the
focused orchestration workload: it could not prove exactly one matching
`server_ready` registration. There are no task or worker events to evaluate
from this attempt. The exact session was interrupted and removed without
touching the stable profile or the default tmux server. This retry is neither a
tool success nor evidence that the live workflow is ready.

## Post-phase-separation focused retry — 2026-08-29

The runtime receipt phase separation allowed the initial exact-session
`server_ready` gate to pass in a real attached session. After the one permitted
semantic workload submission, however, the same event reader became
unavailable while the pane was working. Because hidden events were no longer
auditable, the verifier did not answer the requested clarification or submit a
recovery message. The attempt is **failed**, and no later orchestration stage
is accepted.

## No-bytecode candidate route retry — 2026-08-29

The repeated registration reads remained stable, but the first coordinator
activity after the one workload delivery was local exploration and skill
reading rather than `open_task`; no task-opening event appeared. This violates
the mandatory first-action route invariant. The verifier stopped the owned
session without answering the clarification or attempting recovery. No later
workflow stage is accepted.

## Corrected-bootstrap focused retry — 2026-08-29

The packaged bootstrap exception was applied. After stable repeated
registration reads and one workload submission, the coordinator did not invoke
`open_task` during a full bounded live turn. No prohibited project-facing
activity was observed, but the event stream remained registration-only. This is
a genuine first-call route-execution failure; the session was stopped without a
user-answer injection or recovery workload.
