# Official Codex lifecycle audit (2026-08-29)

Status: architecture review plus source implementation. The lifecycle
observer is now bundled; candidate and attached-live qualification remain
separate gates and were not run by this source-only review.

This review compares the current Cortex design with the current official
Codex documentation. It intentionally does not use repository history. The
official references are the OpenAI Codex documentation and the OpenAI-owned
`openai/codex` and `openai/plugins` repositories.

## Executive conclusion

Cortex should remain a domain state machine implemented by its MCP server. It
must not try to become a second Codex host, scheduler, subagent runtime, or
hook engine. Codex owns sessions, turns, prompts, subagent creation and
continuation, permission inheritance, transcripts, compaction, and process
lifecycle. Cortex owns only durable task-domain facts: task contracts,
clarification/approval bindings, assignments, evidence, immutable
publications, idempotency, and advisory governance.

The current architecture is directionally correct on this separation. Two
release-blocking host-boundary issues require explicit fail-closed behavior:
selected route state must be scoped to the official session and turn, and a
generic packaged-skill `Read` cannot be trusted merely because its path looks
safe. The bundled hooks observe lifecycle events, but Codex does not expose an
authenticated file identity or inode/content capability in the documented
`PreToolUse` payload.

Do not use hooks as a durable workflow engine. Official documentation says
that `SubagentStart` continuation output does not stop a subagent, while
`SubagentStop` and `Stop` continuation decisions create a new continuation
prompt. Those are host controls, not a replacement for a server-owned
binding. A hook may add context or ask Codex to continue; only the domain
server may decide whether a clarification/approval/report relation exists or
has already been consumed.

## Evidence from official documentation

* Codex discovers hooks from active configuration layers and installed plugin
  manifests/default `hooks/hooks.json`; lifecycle coverage includes
  `SessionStart`, `SubagentStart`, `PreToolUse`, `PostToolUse`, `PreCompact`,
  `PostCompact`, `UserPromptSubmit`, `SubagentStop`, `Stop`, and
  `SessionEnd`. [Codex Hooks](https://developers.openai.com/codex/hooks)
* `UserPromptSubmit` receives the prompt and may add developer context or
  block the prompt. `PreToolUse` can intercept MCP calls, and its supported
  permission output is distinct from the continuation output used by
  `Stop`. [Codex Hooks](https://developers.openai.com/codex/hooks)
* `SubagentStart` can add context, but `continue: false` does not prevent the
  subagent from starting. `SubagentStop` can request continuation and exposes
  the subagent identity and transcript path. [Codex Hooks](https://developers.openai.com/codex/hooks)
* `PreCompact` and `PostCompact` bracket automatic or manual compaction;
  `SessionStart` identifies startup, resume, clear, and compact starts.
  Transcript paths are explicitly not a stable hook interface. [Codex Hooks](https://developers.openai.com/codex/hooks)
* A `Stop` hook continuation is implemented by Codex as a new continuation
  prompt. It is not an in-place mutation of the prior turn and must not be
  confused with a durable Cortex decision. [Codex Hooks](https://developers.openai.com/codex/hooks)
* Subagents inherit the parent permission mode and, when omitted, inherit
  settings such as MCP servers and skills configuration. Codex itself owns
  spawning, routing, waiting, and closing agent threads. [Codex Subagents](https://developers.openai.com/codex/subagents)
* Skills use progressive disclosure: the host initially supplies only name,
  description, and (in Codex) path, then loads the full `SKILL.md` after
  selection. This supports semantic workflow guidance, but makes verbose
  parameter recipes especially harmful. [Build skills](https://developers.openai.com/codex/skills)
* MCP configuration is host configuration in `config.toml`; Codex starts
  STDIO servers and exposes their advertised tools in the TUI. Server-wide
  cross-tool guidance belongs in MCP `instructions`; the tool contract is
  still the authority for a call. [Codex MCP](https://developers.openai.com/codex/mcp)
* A plugin may package a manifest, skills, MCP servers, hooks, agents, and
  other supporting surfaces. [OpenAI plugins repository](https://github.com/openai/plugins/blob/main/README.md)

## Lifecycle ownership matrix

| Lifecycle capability | Official Codex owner | Cortex responsibility | Current assessment | Priority |
|---|---|---|---|---|
| Plugin discovery, skill selection, progressive disclosure | Codex host | Supply concise semantic skill metadata | Correct boundary; do not duplicate host loading | P2 |
| Explicit route activation and first task anchor | Codex prompt/tool loop plus Cortex hook guardrail | Persist one task contract and reject unanchored domain work | Sound; hook is a guardrail, not sole enforcement | P1 |
| MCP server startup, tools/list, advertised schemas | Codex MCP client/server protocol | Keep one canonical catalogue and stable descriptions | Must be verified in the isolated candidate before live | P1 |
| Main session, turn, prompt submission | Codex host | Observe activation and correlate task identity | Current hook observes prompt, but not session start | P1 |
| Native subagent spawn and configuration inheritance | Codex host | Persist assignment and expected worker outcome | Domain assignment is appropriate; do not emulate spawn | P1 |
| Subagent start/stop/continuation | Codex host hooks | Observe identity/state; correlate to assignment; never synthesize a child | Bundled observation; candidate/live proof pending | P1 |
| User clarification question | Model + Codex interaction | Open one durable hold before question; record exact answer binding | Correct server-owned design; host follow-up remains host-owned | P1 |
| Plan approval / steering | User/model + Codex interaction | Persist binding, revision, and idempotent decision | Correct; no hook should auto-approve | P1 |
| Worker report publication | Worker model through MCP | Immutable publication, evidence lineage, replay/conflict semantics | Correct; event journal must include worker lifecycle correlation | P1 |
| Worker verification and rework | Coordinator/model | Store reports and relations; choose rework/verification depth | Correct advisory/domain split | P2 |
| Documentation impact and sync | Coordinator/worker skills | Persist worker-owned impact evidence and publication | Correct; do not turn documentation into a host hook | P2 |
| Governance/adaptation | Model-owned advisory reasoning | Persist advisory result only | Correct; server must not schedule waves or gates | P2 |
| Compaction and recovery | Codex host | Observe boundaries; reconcile from durable domain state | Bundled observation; candidate/live proof pending | P1 |
| Stop / session end | Codex host | Record honest incomplete/closed domain state | Existing Stop guardrail is valid but cannot prove session end | P1 |
| Tmux/live-dev transport | Operator/helper outside Codex lifecycle | Deliver literal input and expose observations | Correct only if helper remains transport-only | P1 |

## Architectural findings

### P0 — lifecycle evidence is incomplete (resolved in source; candidate/live gate pending)

The source implementation now registers `SessionStart`, `SessionEnd`,
`SubagentStart`, `SubagentStop`, `PreCompact`, and `PostCompact` alongside the
activation hooks. `cortex_lifecycle_observer.py` writes only sanitized,
one-way-correlated lifecycle observations into the validated candidate
generation. It does not inspect transcript or prompt contents, and it never
changes host or domain state. Candidate packaging and black-box source tests
are green; candidate and attached live qualification remain required before
release.

The host-side seam now observes the official `Agent` local-function path at
`PreToolUse`/`PostToolUse`. It recognizes only the server-issued `dc_`
dispatch marker shape and hashes it before journaling. Official Codex exposes
the Agent tool invocation and the later `SubagentStart` event separately, but
does not expose an authenticated relation between them. Therefore the seam
records `unavailable`/`ambiguous` capability state and deliberately does not
bind the marker to a native agent identity. Model-text echo, later worker MCP
calls, a successful Agent result, or a stop event cannot manufacture that
missing relation. A successful correlation can be added only if a future
supported host event supplies both sides under one authenticated invocation
context.

Worker bootstrap has a separate domain invariant: the server-rendered worker
brief must be delivered byte-for-byte, and the fresh worker's first semantic
action is assignment-evidence consumption through the opaque assignment
anchor. Task reads are not a bootstrap action and cannot substitute for that
consumption. The successful evidence result is the authority for the worker's
subsequent publication flow.

The remaining gate is to prove the same handlers in the content-addressed
candidate and attached live session, including stale/revoked/tampered leases,
restart, duplicates, concurrent workers, and compaction ordering.

### Bootstrap binding decision (2026-08-30)

The worker bootstrap capability is now a server-private lease, not a model
argument and not a value rendered into the native worker message.  The public
worker operation accepts the compact assignment locator; the server resolves
the matching private capability row by task, assignment, contract revision,
package provenance, and dispatch digest inside one SQLite write transaction.
The `minted -> consumed` transition is conditional and therefore produces one
continuation under concurrent calls; replay returns that same continuation.
An active or expired lease continues to control parent replacement through the
existing stale/conflict reconciliation path.

The current Codex hook payload exposes `tool_use_id` at `PreToolUse` for the
native spawn and session/turn/agent fields at `SubagentStart`; it does not
expose one authenticated field joining those two events. That absence means a
hook must not grant worker authority or rewrite the later MCP evidence call.
Controlled host probes also established that rewriting a native spawn through
`PreToolUse.updatedInput` crosses an unsupported encrypted transport boundary:
the spawn can report success while the child stops before MCP initialization.
`PostToolUse(open_assignment)` therefore stores the server-issued worker
context in a mode-0600 receipt. `PreToolUse(native spawn)` validates and
atomically claims exactly one receipt for the same coordinator session/turn
without rewriting or overriding the host call. The later `SubagentStart`
delivers the claimed authoritative context through `additionalContext` and
establishes provisional lifecycle correlation; successful server-side
evidence consumption remains the sole transition to worker authority. No hook
rewrites native spawn or MCP arguments, and no bearer bootstrap token is
exposed to the model.

### P1 — hooks must not be treated as enforcement of domain workflow

The activation hook may deny pre-anchor tool calls and add context, but Codex
hooks are host callbacks with event-specific output semantics. They cannot
guarantee that the model calls the right domain tool, that a subagent starts,
or that a continuation was delivered. The MCP server must continue to enforce
all durable invariants and idempotency. Live acceptance must fail when the
required hook evidence is absent, not silently infer it from a report.

### P1 — activation state is session/turn scoped

The activation guard derives its state filename from a one-way session
fingerprint and stores only a one-way current-turn fingerprint. A tool event
with a missing, foreign, or mismatched turn is denied while the route is
selected; official lifecycle events remain observable, and a legitimate
`UserPromptSubmit` advances the current turn without reconstructing domain
state. Raw session and turn identifiers are not persisted. This is a host
guardrail only: the MCP server remains authoritative for task anchoring.

### P1 — generic packaged-skill `Read` has no authenticated TOCTOU seam

Official `PreToolUse` supports a permission decision and, for supported tools,
an `updatedInput` rewrite. The documented event does not provide an
authenticated resource handle, inode identity, or a host guarantee that the
hook's path check and the host's later file open refer to the same bytes.
Therefore a hook cannot make an atomic same-user file-open guarantee from a
path check alone. Per the user's live-dev requirement, the practical bounded
exception is restored only for a verified candidate receipt, a canonical
regular file below the installed `PLUGIN_ROOT/skills` tree, and an optional
per-file digest membership map when available. Traversal, symlink, outside,
changed-content, and malformed inputs remain denied. This is an explicit
trusted-plugin TOCTOU limitation, not a claim of an atomic guarantee.
Qualified resource/bootstrap tools remain supported independently of this
exception. A future host-provided resource/content identity would remove the
accepted same-user limitation.

### P1 — host continuation is not a Cortex capability

`record_clarification` may return a renderer-owned continuation instruction,
but Cortex cannot invoke a Codex child or inject a host turn. The host must
consume the returned delivery projection using the exact saved assignment and
must report delivery/unavailability. Keep the private delivery adapter only
as an explicit host integration boundary; do not invent a plugin lifecycle ABI.

### P1 — MCP guidance placement is mostly right, but metadata must be audited

Official MCP guidance supports server-level `instructions` for cross-tool
workflow guidance and relies on advertised tool contracts for calls. Keep
semantic sequencing in the skill/server description, but remove every
parameter recipe from model instructions. Ensure the manifest description,
server instructions, tool descriptions, and tool schemas describe the same
catalogue and operation count; stale manifest claims are a distribution
defect even when Python tests pass.

### P2 — compaction state must be resumable without transcript parsing

The existing compaction skill correctly directs the coordinator to recover
from durable records. Add lifecycle observations around compaction and include
the active task/assignment fingerprints in the sanitized journal correlation.
Never make the transcript file a protocol dependency: official documentation
calls that interface unstable.

### P2 — keep host and domain state orthogonal

Do not persist Codex agent IDs as if they were task-domain identities. Store a
one-way correlation from host agent event to assignment fingerprint. A host
restart, fork, or continuation may produce a new host identity while the
logical Cortex assignment remains the same; replay rules must be decided from
the assignment and publication relations, not from a guessed agent ID.

## Required pre-live gates

1. Source tests prove the full advertised MCP catalogue through the real
   stdio handler, including first-call schemas and output envelopes.
2. Source tests prove all lifecycle hook handlers with sanitized output and
   no transcript/prompt/report leakage.
3. Candidate refresh proves content identity and isolated configuration, then
   reruns the lifecycle and MCP suites with zero skips, errors, or stale
   compatibility paths.
4. Focused attached live run visibly confirms the TUI, uses the real MCP
   server, and records task anchor → clarification hold → exact host delivery
   → worker continuation → first worker publication. `events` must show
   worker start/stop and no hidden tool errors or unexplained mutation replay.
5. Full multi-turn live run exercises plan approval, implementation,
   independent verification, documentation impact, governance, compaction or
   recovery when triggered, and closure. A final report reference without
   lifecycle evidence is not acceptance.

## What must not change

The audit does not remove any existing orchestration capability: task
anchoring, knowledge routing, assignment/evidence barriers, immutable reports,
clarification and approval decisions, rework, verification, documentation
impact, governance, adaptation, progress accounting, compaction recovery,
content safety, provenance, and closure remain required. The correction is to
make Codex host lifecycle and Cortex domain lifecycle explicit, correlated,
and independently verifiable.
