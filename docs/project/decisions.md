# Decisions

- Keep exactly seven public storage operations and model-owned coordination.
- Allocate typed drafts in the server, bind their short IDs to native caller threads,
  and publish only by ID; keep the same ID in the filename and Markdown marker.
- Put editable drafts below project `.cortex`; reserve `.codex/cortex` for final
  task files written by the MCP server because Codex protects project `.codex`.
- Keep one actual pipeline file, prepend editions, and preserve older text below.
- Store bodies in project task files, only metadata and durable intents in SQLite.
- Keep the coordinator free of project execution: workers own project reads,
  commands, edits and checks apart from its exact pipeline draft. Selected authored
  report opening briefs may inform consequential decisions through Cortex.
- Keep knowledge commands, index routing and rereading after summarization as
  bundled model instructions; do not rebuild a workflow engine around them.
- Keep explicit retention cleanup as a host-side command with project isolation.
- Preserve semantic version 1.15.6 as directed; update only the payload hash.
- Bind tasks to host MCP thread metadata after real CLI/Desktop observation; no model-authored task selector or latest-task fallback.

- Make report-template validation and publication recovery storage guarantees;
  keep evidence-dependent delegation with the model rather than adding a fixed
  stage machine or a second hook-owned workflow.
- Qualify Cortex calls and report lifecycle independently from project development
  diagnostics; retain both without conflating their acceptance boundaries.

- Distribute the 22 complete worker profiles as native plugin skills. Ordinary
  native subagents require host attachment of the exact assigned skill; marketplace operation does not
  depend on global TOML registration or a custom spawn selector. Keep optional
  TOML exports generated from the same source, outside installation prerequisites.

- Publication closes an assignment, not its native context. Explicit completed-worker
  follow-ups create new immutable reports; independent verification uses fresh context.
- Keep graph-first discovery concrete in every generated worker profile. Codebase
  Memory availability never becomes a server gate or permission to widen scope.
- Evaluate outcome quality and resource cost separately from protocol conformance;
  model routing heuristics remain provisional until comparative measurements support them.

Final handoff identity follows publication ownership, not reference count: the latest
report from the same worker is required, and its own earlier publications may be
cited. This preserves useful continuation context while rejecting foreign or stale-only
evidence. Coordinator completion remains a pipeline decision and user response;
the contradictory language-table reference to coordinator-authored synthesis was removed.

- Use standard progressive marketplace skill loading: complete host attachments or
  exact advertised SKILL.md reads, then needed declared Markdown references. Never
  inspect agent TOML or server internals. The initial absolute read prohibition was
  withdrawn after the user clarified their intent and official documentation confirmed
  normal skill-file loading. No custom loader or personal registration is needed.
- Scale assignments to unresolved evidence and risk. A bounded implementation may
  include its own tests and documentation; do not add specialists just to fill categories.
  Acceptance evidence must reach the user's observable boundary, not only internal helpers.
- Do not conflate interruption with slot release. Audit repeated spawn attempts
  after capacity rejection and messages to already completed workers. A prompt
  does not repair the host's resident-context allocator.
