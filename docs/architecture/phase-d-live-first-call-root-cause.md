# Phase D live first-call root-cause analysis

**Status:** failed before the first required Cortex mutation; no new live session was started for this diagnosis.

**Implementation update:** the packaged stdio boundary now emits one passive,
sanitized `server_ready` observation only after a successful physical MCP
initialization reply. It identifies the verified candidate only by build
identity and catalogue count/digest; source and black-box staged-candidate
regressions cover the observation. Candidate refresh and live qualification
remain separate, unrun gates.

**Scope:** the isolated candidate only. This note deliberately excludes raw session records, prompts, project paths, opaque references, report content, and private diagnostics.

## Conclusion

The immediate blocker is **not candidate drift, an invalid Cortex MCP server, or a demonstrated server-start failure**. The live model received the packaged Cortex activation/context but did not make a Cortex MCP call. It instead made ordinary host shell calls, despite the task route and the live workload forbidding that path. The empty MCP journal is therefore consistent with no request reaching the server.

The journal does not, by itself, prove that the variable was inherited by a plugin child: a server which is never called emits no event. That observability limitation must be closed, but it is not the leading explanation for this run.

## Sanitized evidence

| Question | Result | Evidence and confidence |
| --- | --- | --- |
| Was the isolated candidate delivered? | Yes. | The isolated profile points at the content-addressed candidate; the candidate receipt reports matching source and candidate content; the plugin registration is enabled and its Cortex approval setting is present. **High**. |
| Did the candidate contain the packaged route and MCP declaration? | Yes. | The installed package is byte-identical for the manifest, MCP declaration, orchestrator skill, MCP entry point, transport, and event-journal runtime. The manifest declares the bundled skills and the direct Python MCP server. **High**. |
| Is the candidate MCP server itself usable? | Yes. | A read-only isolated stdio handshake completed and `tools/list` returned the complete 15-operation catalogue with input and output schemas. The server returned provenance with the candidate build identity. **High**. |
| Was the packaged Cortex route present in the ordinary Codex turn? | Yes. | Sanitized session-structure analysis found the bundled orchestrator/control context and Cortex operation names in the turn inputs. The pane also rendered an activation acknowledgement. That acknowledgement is not a durable activation action. **High**. |
| Did any Cortex operation reach the server? | No. | The exact-session event journal remained empty and the sanitized session summary contains no Cortex MCP tool invocation, `tools/call`, tool error, or startup failure. Its executed calls belonged to the ordinary host shell family. **High**. |
| Did MCP startup fail? | No supporting evidence. | A startup failure before a call and a model that never chooses a Cortex tool are both compatible with an empty journal. The latter is independently established by the session call summary. **Medium-high**. |
| Was journal environment propagation proven? | No. | The launcher exports the journal path before `cortex-dev`, and `cortex-dev` does not clear it. But there is no host-child startup attestation, so the inherited environment cannot be proven from an empty journal. **Medium**. |

## What happened

1. The ordinary Codex session started from the isolated profile and had its composer visibly confirmed.
2. The model produced an ordinary-text statement that it was activating Cortex.
3. It did not follow that statement with `open_task` or any other Cortex MCP operation. Its observed executable activity was ordinary host shell activity instead.
4. Because no MCP request reached `mcp_api.serve_stdio`, `EventJournal.emit` had nothing to write. There was no task, assignment, clarification, worker, or report boundary to inspect.

This is a first-call route-execution failure. It is not a tool-schema failure and must not be repaired by adding MCP parameter recipes to a prompt or skill.

## Architectural defect

Today, live qualification treats three different facts as if they were one:

```text
candidate installed + skill text supplied + model says "activated"
    !=
ordinary Codex has registered the candidate MCP catalogue
    !=
model selected the Cortex route and issued the required first operation
```

Candidate provenance proves only package bytes. A plain-language activation sentence proves only model text. The event journal observes a call only after the server is selected. There is no single host-owned activation receipt joining those three boundaries, and the journal has no explicit process-start/registration observation.

The existing orchestration capabilities do **not** need to be removed or moved into a backend scheduler. Tasks, advisory governance, assignments, native worker dispatch, clarification holds, report publication, verification, documentation impact, and closure retain their current ownership. The missing component is a narrow live-host activation boundary.

## Required root correction

Implement a **host-owned Cortex live activation receipt** for the exact isolated candidate, before the workload prompt is accepted:

1. `cortex-live-smoke start` continues to create only the ordinary tmux/Codex session; it must not interpret task state or issue task mutations.
2. The ordinary Codex host must expose a safe registration observation for the exact plugin MCP server after it has initialized the candidate: candidate build identity, registered server identity, and count/list digest of the advertised catalogue. It must contain no prompt text, arguments, references, paths, reports, or private logs.
3. The live verifier (the LLM, not the transport) must visibly observe that receipt before submitting the workload. Its absence is a failed/unverified environment, not an invitation to send a task prompt.
4. The router contract must make a Cortex-selected task semantically atomic at the first boundary: the first project-facing action is the catalogued task-opening operation. A prose activation claim or a host shell/repository action before that operation is a route violation. This is an orchestration invariant, not parameter teaching.

The bundled route contract now records this invariant explicitly: activation
acknowledgement is not activation, and the first project execution action after
explicit route selection must be `open_task`. The coordinator may compose the
outcome contract beforehand, but must stop on a task-opening failure or missing
task anchor rather than dispatching degraded work. The corresponding passive
receipt and surface-parity matrix is maintained in
`docs/architecture/contract-cleanup.md`.
5. The journal should additionally emit a single safe `server_ready` observation after successful candidate verification/initialization, with no tool outcome. This distinguishes “ordinary Codex did not start the candidate server” from “the model did not call it,” while preserving the journal’s observation-only role.

The host receipt is the root correction because it establishes that the *actual ordinary Codex process* registered the exact current candidate. The server-ready observation then establishes process startup, and the existing per-call journal establishes tool use. No layer needs to infer the next model action.

## Affected surfaces

- `scripts/cortex-live-smoke`: add a passive, exact-session-readable host registration receipt and keep it transport-only.
- `scripts/cortex-dev`: carry only the already-proven candidate identity into the host receipt; do not alter the stable profile.
- `plugins/cortex/scripts/cortex_runtime/mcp_api.py` and `event_journal.py`: emit one sanitized server-ready observation after successful MCP initialization.
- Bundled `orchestrator` and `cortex-control` contracts: assert semantic first-call ordering and reject prose-as-activation. They must not name MCP parameters or shapes.
- Source/candidate/live tests: verify receipt provenance, no secret/raw-data leakage, candidate registration mismatch failure before workload delivery, one server-ready event, catalogue parity, and a focused live route where the first project-facing action is exactly one successful task open.

## Decision matrix for the next focused run

| Host registration receipt | Server-ready observation | First Cortex call | Interpretation |
| --- | --- | --- | --- |
| Missing | Missing | Missing | Candidate not demonstrably registered in ordinary Codex; fix host/plugin loading. |
| Present | Missing | Missing | Plugin registration exists but MCP process did not initialize or journal inheritance is broken. |
| Present | Present | Missing | Model route violation; do not blame cache or MCP startup. |
| Present | Present | Failure | Real first-call server/schema/service defect; use the sanitized event and fix the advertised contract/runtime. |
| Present | Present | One success | Continue LLM-driven multi-turn verification. |

## Confidence and limits

**Root cause confidence: high (about 0.9).** Candidate/configuration parity and standalone MCP catalogue are proven; the session made no Cortex invocation but did make ordinary shell calls. We cannot state that the journal environment was propagated to a plugin child because the child was not observed starting. The proposed receipt plus server-ready observation removes that remaining ambiguity in the next run.

No production or test file was changed during this diagnosis. No stable profile was read or modified, and no additional live session was started.

## Reclassification of the no-bytecode focused retry — 2026-08-29

The retained sanitized pane evidence does **not** establish project repository
inspection or shell validation before task opening. The only individually
identified action was a read of the packaged `cortex:orchestrator` skill. The
pane's aggregate “commands” count does not disclose command bodies and must
not be reclassified as shell or project activity without evidence. The fresh
test project contained only the fixture files supplied by the verifier; no
project artifact was observed or changed by the coordinator before cleanup.

That packaged-skill read is control-plane bootstrap. The host-level skill
system requires the selected bundled contract to be loaded before task actions,
and the bundled route describes the *first project execution action*—not the
first host control-plane action—as `open_task`. The bundled text also says
activated skill bodies are host-supplied and already loaded, so the host should
normally avoid a visible reread; nevertheless, such a packaged-contract load
is not repository inspection, shell validation, project-state checking, or
worker dispatch.

### Operator rule

Before `open_task`, allow only necessary host/package control-plane bootstrap:
selection/loading of the explicitly requested bundled Cortex contract and the
passive candidate registration observation. Never allow project-root or
repository inspection, project-local document/code/configuration reads, shell
validation, project-state checks, Git activity, or worker dispatch. The first
project-facing action remains exactly one `open_task`.

The prior stop was therefore premature **if** its three visible actions were
only the packaged bootstrap indicated by the sanitized pane. It was not proof
of a route violation. A focused live retry may proceed without a runtime code
change, but the operator/live qualification contract must adopt this precise
control-plane exception so packaged skill loading is not treated as project
work. If later pane or event evidence identifies a project read or shell
command before `open_task`, that remains an immediate failure.
