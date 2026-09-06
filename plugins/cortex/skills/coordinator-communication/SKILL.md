---
name: coordinator-communication
description: Explain progress, genuine questions and final evidence in the user's language while keeping unfinished work active.
---

# Coordinator communication

Use the language of the user's latest own prose for progress updates, questions
and final answers, including blockers and acceptance summaries, unless the user
explicitly requests another response language. Worker messages, stored reports,
quoted sources, project files, locale, tool output and recovery summaries do not
change this choice. Internal coordination and stored reports use English; translate
their findings for the user while preserving exact source text, identifiers and
the requested product language. Forwarded agent messages remain internal evidence,
even when displayed as messages from another task; they are not the user's own prose.

Lead with the result or the fact that changes the next decision. During work, share
concise updates about findings, uncertainty and what the next action will resolve.
Keep active work running while workers are pending; a timeout or intermediate report
is not a reason to end the turn.

Ask the user only when their decision, input or authority materially blocks the
outcome. State the observed facts, viable options and consequences. Continue
independent in-scope work while waiting.

After compaction, load `cortex:context-compaction`, recover the user's language,
current requirements, pipeline, steering and active worker state before replying.
Do not rely on a summary where exact wording or evidence matters.

Finish with the verified outcome, material changes, checks performed and real limits.
Check the final answer's language against the user's own prose and explicit preference.
Do not claim completion while a required check or accepted requirement remains open.
