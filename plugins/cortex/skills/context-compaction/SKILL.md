---
name: context-compaction
description: Produce concise, evidence-based handoffs between Codex agents or work phases. Use when a task crosses agents, resumes after long investigation, or has noisy logs and reports that would otherwise pollute the parent context.
---

# Context Handoff

Do not pass raw transcripts by default. Create a compact handoff containing only:

1. Goal and acceptance criteria.
2. Verified repository facts with file and symbol references.
3. Decisions made and alternatives rejected.
4. Changed files or current diff state.
5. Commands run and decisive outputs.
6. Open questions, blockers, and next action.

Use short summaries for older logs and reports. Preserve exact error output only when the next agent needs it to reproduce or diagnose the issue. Never summarize secrets into the handoff. The parent thread should integrate the handoff; do not depend on private host databases or resume a failed subagent session.
