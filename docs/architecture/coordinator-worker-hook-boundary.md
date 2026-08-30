# Coordinator and worker hook boundary

Status: **accepted development architecture, 2026-08-29**

## Decision

The coordinator bootstrap is not enforced through `PreToolUse`. Codex reads
selected skills through its ordinary host mechanism. `UserPromptSubmit` does
not inject skill contents, and coordinator `Read`, `Bash`, and other local
tools are not classified or rewritten by Cortex before task opening.

The first Cortex MCP mutation remains task opening. The MCP backend owns task
identity, mutation ordering, command receipts, replay behavior, and every
later task-scoped relation. A generic host hook cannot reliably classify the
semantic purpose of a coordinator shell or read call and must not manufacture
failures by attempting to do so.

`PreToolUse` retains one narrow enforcement role for native workers. Before a
worker has successfully consumed its server-issued assignment evidence, local
project tools are denied because those mutations are invisible to the MCP
backend. After successful consumption, normal assignment-scoped project work
is allowed. Coordinator attempts to perform worker-owned consumption or
publication remain denied.

## Removed coordinator machinery

- skill-content injection from `UserPromptSubmit`;
- safe-reader and rewritten-input flows;
- command and path parsing for skill reads;
- trusted-read categories and failure states;
- pending, issued, consumed, or one-shot skill-read state;
- pre-task coordinator blocking of `Read`, `Bash`, and project inspection.

## Ownership matrix

| Boundary | Authority | Hook behavior |
| --- | --- | --- |
| Selected coordinator skill loading | Codex host | Passive; no injection or interception |
| First Cortex task mutation | MCP backend | Backend accepts task opening and rejects invalid unanchored relations |
| Coordinator local reads before task opening | Coordinator model and host permissions | Passive; no semantic classification |
| Native worker before assignment consumption | Cortex worker guard | Deny local project tools; allow only assignment-evidence bootstrap |
| Native worker after successful consumption | Assignment contract and host | Allow assignment-scoped local work |
| Worker-owned report publication | MCP backend plus worker guard | Owning worker only |
| Coordinator attempt to consume or publish as worker | Worker guard | Deny |
| Candidate provenance and event capture | Isolated live-dev launcher/runtime | Observe and fail closed on provenance mismatch |

## Acceptance evidence

1. Route selection emits no injected skill bundle.
2. Coordinator skill reads and shell reads are not blocked before task opening.
3. Task opening is the first successful Cortex MCP mutation in a clean live
   orchestration run.
4. A native worker cannot use local project tools before successful assignment
   evidence consumption.
5. The same worker can use its scoped tools after consumption.
6. Coordinator consumption/publication of worker-owned evidence remains
   impossible.
7. No retired trusted-reader vocabulary remains in hook code, event schemas,
   launcher environment, or tests.
