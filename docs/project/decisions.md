# Decisions

- Keep exactly seven public storage operations and model-owned coordination.
- Allocate typed drafts in the server, bind their short IDs to native caller threads,
  and publish only by ID; keep the same ID in the filename and Markdown marker.
- Put editable drafts below project `.cortex`; reserve `.codex/cortex` for final
  task files written by the MCP server because Codex protects project `.codex`.
- Keep one actual pipeline file, prepend editions, and preserve older text below.
- Store bodies in project task files, only metadata and durable intents in SQLite.
- Keep the coordinator free of project execution: workers own all project reads,
  commands, edits and checks apart from the coordinator's exact pipeline draft.
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
  native subagents load the exact assigned skill; marketplace operation does not
  depend on global TOML registration or a custom spawn selector. Keep optional
  TOML exports generated from the same source, outside installation prerequisites.
