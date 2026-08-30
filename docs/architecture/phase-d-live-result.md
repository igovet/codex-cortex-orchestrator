# Phase D focused LLM-driven live result

Status: **failed/unverified** — no semantic decision or worker stage was
reached. This record is sanitized: it contains no raw prompt, user content,
opaque identifiers, event payloads, private paths, or diagnostic log text.

## Scope

The verifier used the repository-supported isolated candidate launcher and the
exact named default-server tmux session. It attached a real current-user tmux
client through a real PTY, observed the actual Codex screen, and used the
transport only for literal input and the one permitted trust acknowledgement.
The stable plugin/profile was not touched.

The candidate delivery/provenance preconditions rendered successfully before
ordinary Codex started. The isolated candidate reported matching source and
candidate content identities. This establishes delivery provenance only; it
does not establish a semantic live pass.

## Attempts

### Bootstrap observation

The first fresh project reached a visually rendered composer and subsequently
revealed the Codex fresh-directory trust prompt before the workload could be
accepted. The initially submitted text could not complete its standalone
submission key because the exact session exited from that trust screen. No
Cortex event, task, worker, or mutation occurred. The attempt is an
unverified bootstrap failure, not a tool result and not a retry of a mutation.

### Focused observed attempt

The verifier created a separate fresh project and exact named session. It:

1. attached the output-only pane observer before releasing the fixed isolated
   launcher;
2. attached a real tmux client and visibly observed the fresh-directory trust
   screen;
3. sent exactly one explicit trust acknowledgement after that observation;
4. reattached and visibly confirmed the ordinary interactive composer;
5. submitted one task-specific, semantic-only Cortex orchestration prompt via
   the supported transport; and
6. observed the coordinator pane and the bounded sanitized MCP-event stream
   throughout the run.

The pane accepted the route and stated an intent to start the worker-only
workflow. It then remained in visible model-working state. No Cortex MCP event
was emitted in the exact session's sanitized observation stream. In particular,
the verifier observed none of the required task opening, clarification hold,
clarification recording, assignment, same-worker continuation/recovery,
publication, independent verification, documentation, or closure evidence.
There was therefore no worker event stream to inspect for a first publication.

The session was interrupted only after bounded observation showed no progress
to a first Cortex event. The exact named session was removed; the default tmux
server and the stable profile were preserved. A clean launcher exit marker was
not available after failure interruption.

## Gate result

| Gate | Result |
| --- | --- |
| Isolated candidate/provenance before Codex | Passed |
| Real tmux attachment | Passed |
| Fresh-directory trust acknowledgement | Passed once after visible observation |
| Composer before workload input | Passed |
| One literal workload submission | Passed |
| First Cortex MCP event | **Not observed** |
| Durable clarification hold/record | **Not observed** |
| Worker assignment/continuation | **Not observed** |
| First worker publication event | **Not observed** |
| Tool/schema/traceback event | Not observed because no MCP event occurred |
| Focused live acceptance | **Failed/unverified** |
| Exact-session cleanup | Passed |

## Disposition

This result does not invalidate the Clarification Hold architecture decision;
the session never reached that boundary. It exposes an earlier live execution
blocker: after route acknowledgement, ordinary Codex made no observable MCP
progress. Do not treat the absence of an error event as success.

Before the next focused run, reproduce this no-first-event state with a bounded
candidate-side startup/host observation test, then fix the owner of that
startup/route handoff. The next live run must again begin from a fresh isolated
candidate and must not send a second workload message to an already-working
coordinator. Once the first MCP event is observed, the Clarification Hold,
answer recording, exact-worker delivery, and first publication gates in
[Phase D live decision root cause](phase-d-live-decision-root-cause.md) remain
mandatory.

## Passive-activation requalification — 2026-08-29

**Status: failed/unverified before workload submission.** This later focused
attempt used a fresh separate test project, the current content-addressed
isolated candidate, the required default-server `cortex-v12-smoke` tmux
topology, and a real `TERM=xterm-256color` attached client. It did not submit a
workload and did not create a task.

The LLM visibly observed all of the following before applying the new hard
precondition:

| Boundary | Sanitized observation | Result |
| --- | --- | --- |
| Candidate delivery | The isolated launcher completed candidate refresh and printed agreement between server version, candidate build, source digest, and candidate digest. | Passed. |
| Human TUI route | The attached client visibly showed the trust decision, received exactly one explicit acknowledgement after that observation, and then showed the interactive composer. | Passed. |
| Ordinary-host MCP startup | The composer displayed bounded MCP-server startup activity. | Observed, but not sufficient. |
| Passive Cortex registration | The exact-session owner-only event stream contained no `server_ready` observation after bounded startup observation. | **Failed.** |

The live rule requires exactly one `server_ready` event whose candidate build,
catalogue count, and catalogue digest agree with the current receipt before a
workload is sent. Because that event was absent, no semantic task prompt,
`open_task`, clarification, worker, report, plan review, implementation,
verification, documentation-impact assessment, or closure was attempted. The
LLM stopped only the owned exact session with interruption cleanup; the tmux
server was not killed and the stable profile was not modified. No clean
live-dev exit marker was applicable because the run was intentionally
interrupted at the missing-precondition boundary.

This result narrows the remaining defect to ordinary-host MCP registration or
startup-observation propagation. It does not establish a model route violation
and must not be repaired by weakening the precondition or by adding MCP
argument recipes to prompts or skills. The required next diagnosis is why the
ordinary Codex startup did not yield the packaged server's passive ready event.

## V19 exact-session lease retry — 2026-08-29

**Status: failed/unverified before workload submission.** A new, separate
canonical test project was used with the supported `cortex-live-smoke` start
route and a real attached tmux client. The launcher refreshed the isolated
candidate successfully and visibly reported agreement between the base server
version, content-addressed candidate build, source digest, and candidate
digest. The fresh-directory trust prompt was visibly observed and received one
explicit acknowledgement; the interactive composer subsequently rendered.

No task prompt was sent. At the mandatory pre-workload gate, the helper could
not expose the runtime-owned, nonce-bound observation generation. Thus there
was no inspectable `server_ready` registration record with which to compare the
authoritative candidate receipt and catalogue identity. This is a hard
environment-observation failure, not an absence-of-error pass and not a model
or orchestration-route result. No task, assignment, clarification, worker,
report, plan, implementation, verification, documentation assessment, or
closure was created. The verifier stopped only the exact named session using
interruption cleanup; it did not kill the tmux server or modify the stable
profile. A clean zero-status launcher exit was not available after that
intentional failed-gate interruption.

The next correction must make the verified candidate's runtime-owned
generation readable by the exact-session observer before workload delivery.
Do not weaken this receipt requirement and do not compensate with prompt or
skill instructions about MCP request fields.

## Post-phase-separation focused retry — 2026-08-29

**Status: failed after one workload submission; no orchestration outcome is
accepted.** The fresh isolated candidate reached a real attached ordinary
Codex composer after the visible trust acknowledgement. Before task delivery,
the exact nonce-bound observer successfully exposed exactly one `server_ready`
registration whose build and catalogue identity matched the visible candidate
receipt. This proves the initial runtime receipt boundary was working.

The verifier then delivered exactly one semantic Cortex orchestration prompt.
While the pane showed the coordinator in working state, the next exact-session
event read again returned the sanitized unavailable result. The verifier could
therefore not establish the required exactly-once task opening, inspect hidden
worker calls, or prove a durable clarification. It deliberately sent neither a
clarification answer nor a recovery workload. The exact named session was
interrupted and removed; the stable profile and tmux server were preserved.

This is a stricter failure than the pre-workload case: a receipt reader that
works at startup but can become unavailable while the session is running is
not a durable live-observation boundary. Do not claim any plan, task, worker,
or tool success from the visible working indicator alone.

## No-bytecode candidate focused retry — 2026-08-29

**Status: unverified after premature stop, not a proven route violation.** The
no-bytecode candidate passed isolated provenance, attached-client
trust/composer, and two consecutive exact-session reads of the same
receipt-matching `server_ready` registration. The only identified action after
the one semantic workload submission was loading the packaged
`cortex:orchestrator` skill. That is necessary host control-plane bootstrap,
not project repository inspection or shell validation. The aggregate command
count did not reveal command bodies and cannot honestly be classified further.

The session was stopped before `open_task`, so no task, worker, clarification,
or implementation result is accepted. The next retry must permit required
packaged-skill bootstrap but still fail immediately on any project/repository
inspection, shell validation, project-state check, or worker dispatch before
the exactly-once task opening.

## Corrected-bootstrap focused retry — 2026-08-29

**Status: failed at the real first-call timeout.** The corrected operator rule
allowed packaged control-plane bootstrap and required two stable matching
registration reads before one semantic workload was submitted. During a full
bounded live turn, the pane remained working with no visible project
inspection, shell validation, or worker dispatch. The exact event stream also
remained stable but contained only `server_ready`; no `open_task` appeared.
This is a genuine first-call route-execution failure, not a bootstrap
classification or observation failure. The exact session was stopped without a
clarification answer or recovery workload; no workflow result is accepted.
