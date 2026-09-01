# Official Codex activation-kernel design

Status: **implemented and qualified for the Cortex 1.14.9 supported-host
boundary**

The original activation kernel has since grown a bounded native-worker
audience guard. Its owner-only state contains route booleans plus sanitized
routing categories and correlation digests. It never stores prompt text, task
or worker locators, native message plaintext, assignment bodies, reports,
credentials, or raw tool output. The MCP server independently commits
monotonic connection roles and ledger authority; hook success alone never
authorizes worker project work or publication.

This design follows the current official OpenAI Codex documentation for
[Build skills](https://learn.chatgpt.com/docs/build-skills) and
[Hooks](https://learn.chatgpt.com/docs/hooks). The official pages were fetched
on 2026-08-29 and are the source of truth for the packaging and hook
boundaries described here.

## Official constraints applied

The Build skills guidance says that Codex initially sees only skill metadata,
then loads the selected `SKILL.md`; skills are directories with optional
reference resources, and focused imperative instructions are preferred. It
also documents `agents/openai.yaml` as the place for invocation policy and
tool dependencies. Therefore the activation kernel is deliberately small and
focused, while the orchestration engine remains a post-anchor reference
surface.

The Hooks guidance documents plugin-bundled `hooks/hooks.json`, exact hook
definition trust review, command hooks receiving JSON on standard input, and
the plugin-provided `PLUGIN_ROOT` and `PLUGIN_DATA` environment variables. It
also defines the boundaries used here:

- `UserPromptSubmit` may return additional developer context and is the only
  hook that recognizes explicit route-selection text;
- `PreToolUse` may deny a supported local or MCP tool while the route is
  selected but unanchored;
- `PostToolUse` observes the completed task-opening result and may add context,
  but cannot undo its side effects;
- `Stop` may request one continuation, with a bounded stop guard preventing a
  loop.

The hook commands use no MCP invocation recipe. The active Cortex catalogue
and each advertised tool description remain the sole authority for task-call
arguments.

## Activation kernel contract

The kernel has one purpose: establish that an explicitly selected Cortex route
has exactly one successful task-opening boundary before the full orchestration
engine is enabled.

```text
ordinary turn
  └─ explicit cortex:orchestrator selection
       └─ UserPromptSubmit adds concise activation context
            └─ selected + unanchored
                 ├─ PreToolUse allows bootstrap and task opening only
                 ├─ PreToolUse denies project/repository/shell/dispatch work
                 ├─ PostToolUse marks anchored only after successful opening
                 └─ Stop continues at most once with an activation reminder
                      └─ anchored → full orchestration engine
```

The hook never calls the task-opening operation. The model must make that call
using the live advertised schema. Coordinator activation state is stored below
`PLUGIN_DATA` under digest-named session/turn paths. Native dispatch receipts
progress atomically through bounded digest-only lifecycle states; the worker
MCP process resolves the same owner-only package data root through `CODEX_HOME`
when the hook-only `PLUGIN_DATA` variable is absent. Prompt text, locators,
native message plaintext, assignment bodies, reports, credentials, call
arguments, and raw tool output are never stored.

An opening result that is transport-ambiguous does not cause the hook to issue
another opening. The coordinator performs read-only reconciliation using the
existing receipt/anchor machinery. If commitment is not proven, the route
stops honestly.

## Package surfaces

| Surface | Responsibility | Trust boundary |
| --- | --- | --- |
| `agents/openai.yaml` | Explicit invocation policy and Cortex MCP dependency metadata | Declarative packaging metadata only |
| `hooks/hooks.json` | Official plugin-bundled lifecycle registration | Requires Codex trust review for changed definitions |
| `hooks/cortex_activation.py` | Session-scoped selection, anchor observation, native child audience binding, bounded stop continuation | No runtime imports, no MCP calls, digest/category-only private persistence |
| Lean orchestrator skill | Explicit selection, bootstrap, first-call ordering, stop/reconcile rule | Loaded before the task anchor |
| Post-anchor engine reference | DAG, routing, governance, workers, evidence, clarification, documentation, verification, closure | Loaded only after anchor |
| Full control reference | Semantic catalogue and post-anchor worker/control rules | Loaded only after anchor |

## Feature-preservation requirement

The kernel is not a scheduler and does not replace the existing engine. After
the anchor, every capability in
[`orchestration-feature-parity.md`](orchestration-feature-parity.md) remains
available: bounded knowledge routing, dynamic DAG and parallel ownership,
model routing, advisory governance, planner and plan review, clarification and
steering holds, native worker lifecycle, typed evidence and publication,
documentation impact, hidden-worker verification, initiatives, closure, final
synthesis, and isolated live-dev verification.

The machine-checked parity map must identify each former orchestrator/control
section and its post-anchor reference destination. A missing destination is a
release failure, not an acceptable simplification.

## Live qualification evidence

Before a workload is sent in the isolated ordinary Codex session, the
operator/LLM must observe both the exact candidate receipt and the passive hook
activation/registration evidence. The transport exposes observations but does
not parse readiness, route state, task success, replay, or acceptance. The
first observed project execution action must be one successful task opening;
any shell/repository action or worker dispatch before it fails the route gate.

Source and live gates exercise the hook protocol with sanitized events:
explicit selection emits activation context, unselected prompts emit nothing,
unanchored non-bootstrap tools are denied, bootstrap and task opening are
allowed, successful task opening transitions to anchored, failed opening does
not, native dispatch advances through one host-bound child receipt, stored
state contains no raw data, and Stop emits at most one continuation.

## Source references

- [OpenAI Build skills](https://learn.chatgpt.com/docs/build-skills) —
  progressive disclosure, focused skills, imperative instructions,
  `agents/openai.yaml`, and dependencies.
- [OpenAI Hooks](https://learn.chatgpt.com/docs/hooks) — plugin-bundled hook
  discovery/trust, command-hook protocol, event boundaries, and supported
  output behavior.
