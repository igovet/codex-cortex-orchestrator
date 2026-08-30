# Phase D live `server_ready` propagation root cause

**Status:** diagnosis complete; no production/test code and no new live session
were started for this investigation.

This report is sanitized. It excludes raw terminal/session/log contents,
opaque references, project paths, prompts, and private diagnostic values.

## Conclusion

The missing passive readiness record is caused by an **unsupported environment
propagation assumption at the Codex-plugin boundary**, not by candidate drift or
the journal's owner-only filesystem checks.

The tmux launcher exports a per-session journal-path variable before it starts
`cortex-dev`. However, the actual isolated Codex registration for the Cortex
stdio server reports no configured environment map and no declared environment
variables for that subprocess. The package's MCP declaration likewise has only
the command, arguments, and working directory. It contains no supported
mechanism that transfers the launcher's per-session value into the plugin-owned
MCP child.

Consequently, the event observer waits on a file that the registered child was
never configured to use. It is invalid to describe this as proven parent-child
inheritance merely because the outer shell exported the value.

## Direct evidence

| Boundary | Observation | Result |
| --- | --- | --- |
| Candidate/package | The current isolated registration is enabled and points to the content-addressed Cortex package. The package and source agree for the MCP declaration, server entry point, transport, and journal implementation. | Candidate delivery is not the defect. |
| Actual Codex MCP registration | The current ordinary-Codex MCP inspection reports the Cortex stdio command and its package working directory, with `env = null` and an empty `env_vars` collection. | The registration has no declared route for the session-specific journal value. |
| Package validation | The marketplace validator requires the MCP declaration to equal the three-field command/arguments/working-directory object exactly. Any environment declaration would currently be rejected. | The delivery contract itself prevents an explicit propagation configuration. |
| Journal filesystem boundary | The isolated home, Codex home, and journal root are regular owner-only directories. A controlled candidate stdio initialization using the exact root and an explicit session-style journal path produced one safe readiness record. | Path ancestry, ownership, mode checks, and journal writing are not the blocker. |
| No-explicit-path control | With the same exact isolated root but no explicit journal-path variable, the candidate still initialized and wrote only to its deterministic default journal location, never to the session-specific path. | The runtime behaves as designed when the custom variable is absent. |
| Live observation | The focused session's pre-created session-style journal stayed empty; no default-style journal child was found after that run. Sanitized host logs show MCP registration/initialization and tool-catalog activity but do not expose the child environment. | The record cannot distinguish a filtered `CODEX_HOME` from a host initialization failure, but it does prove that the observer's explicit path was not used. |

The direct no-explicit-path control also establishes that an absent or filtered
custom variable does not cause a journal filesystem rejection. It changes the
destination. The live helper watched only the explicit destination.

## What is known and what remains intentionally unclaimed

Known:

- The candidate MCP server is registered and the package exposes the required
  stdio command.
- The candidate server can complete a physical initialization and emit one
  `server_ready` record with the exact isolated journal root and explicit
  configuration.
- The journal accepts the owner-only path topology created by the live helper.
- The actual Codex registration has no declared subprocess environment
  configuration, while the helper relies on one dynamic shell export.

Not claimed:

- Whether the current Codex host removes every inherited variable or only the
  custom one.
- Whether `CODEX_HOME` was present in the actual child. The absence of a
  default-path file is compatible with a filtered base environment or with an
  initialization that did not reach the post-reply observation point.
- A host bug. Environment forwarding is a host contract; this package had no
  explicit configuration or tested guarantee for the dynamic value.

The installed `codex mcp add --help` command documents a distinct explicit
environment-setting facility for stdio servers. The current official OpenAI
documentation search did not identify a published guarantee that arbitrary
parent-shell environment variables are inherited by plugin-owned MCP children.
Therefore this design must not depend on such inheritance. The general official
MCP guidance only establishes that MCP servers provide model tools; it does not
establish an environment-forwarding contract. See [OpenAI Developers](https://developers.openai.com/).

## Architectural correction

Replace the transport-supplied journal path with a **runtime-owned, candidate
anchored registration channel**. Its identity must be derived by the packaged
MCP process itself from its verified candidate location, not supplied by the
shell and not reconstructed by the model.

Required design:

1. After candidate provenance verification, the MCP runtime derives a private
   journal root from its verified installed package location and verifies its
   containment, ownership, and permissions with the existing no-follow
   descriptor discipline. It must not require `CORTEX_EVENT_JOURNAL_PATH` or
   `CODEX_HOME` as an inherited host contract.
2. Each successful physical initialization atomically publishes a small
   registration receipt in a new runtime-owned generation. The receipt carries
   only the current build identity, catalogue count/digest, and a fresh
   server-generated generation identifier; it contains no task data, prompts,
   paths, references, report data, or errors.
3. `cortex-live-smoke` discovers and exposes only the latest valid receipt for
   the verified candidate generation. It remains observation-only: it does not
   decide readiness, send a workload, or parse an acceptance result. The LLM
   compares the observed receipt with the launcher provenance before proceeding.
4. The helper records the previous generation set before starting the exact
   session. A receipt from that set is stale and cannot satisfy the live gate.
   This preserves exact-session freshness without requiring a host environment
   variable or sharing event streams across runs.
5. Keep ordinary per-call/worker events in the same runtime-owned generation
   after readiness. Their scope is the server generation, not an inferred
   worker lifecycle; the LLM retains all acceptance decisions.

This is the minimum root correction: it removes the undeclared host inheritance
dependency while preserving isolated candidates, real ordinary Codex/tmux
operation, all existing orchestration logic, model-owned decisions, and the
transport-only helper boundary. It does not add MCP parameter examples or
shapes to skills, prompts, or `AGENTS.md`.

## Required verification

| Layer | Required evidence |
| --- | --- |
| Unit | Runtime location derivation rejects symlinks, wrong owners/modes, and paths outside the verified isolated candidate; it needs neither journal-path nor Codex-home environment variables. |
| Candidate stdio | Exact staged candidate emits one fresh registration receipt after initialization with the expected build/catalogue identity, then emits subsequent call events only in that same generation. |
| Host configuration | The package declaration remains valid with the registered server's environment collection empty; no test assumes arbitrary shell-variable inheritance. |
| Live | A real attached ordinary Codex session yields exactly one fresh runtime-owned receipt matching candidate provenance before the LLM sends one workload. A stale receipt, missing receipt, mismatch, or tool error fails without workload delivery. |

No existing orchestration capability is removed. The correction changes only how
the verifier observes a real plugin MCP process before project work begins.
