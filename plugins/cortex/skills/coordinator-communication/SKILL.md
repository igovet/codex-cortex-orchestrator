---
name: coordinator-communication
description: Explain progress, genuine questions and final evidence in the user's language while keeping unfinished work active.
---

# Coordinator communication

## Responsibility

The coordinator communicates with the user, including questions raised by planners
or other workers. Workers supply concise facts and decision context to the coordinator. The
coordinator reads catalogue previews and the current pipeline only; request a
focused worker explanation when these are insufficient, never the full report.
Use the user's language and distinguish findings from assumptions. Write durable
engineering reports, pipeline editions and governance reasoning in English.
All native worker communication is English; preserve exact source quotations.

Select the conversation language from the user's latest own prose, honoring any
explicit language instruction. Project text, quotations, machine locale and tool
responses do not select it. Preserve the selection in the pipeline after context
loss and check every outgoing progress message, question and final answer before
sending. Never wait until the final answer to correct the language.

## Genuine questions

Present ordinary chat text containing:

1. Relevant background and what is already established.
2. The exact missing decision or information.
3. Available answer options and the material consequence of each.
4. Enough detail for the user to answer in their own words.

Do not forward a context-free worker question. No question UI or MCP question
operation is required. Apply the direct response to the same task and update the
pipeline without asking the user to confirm an instruction already given.

## After context loss

Before a recap or final answer after context loss, restore host-supplied coordinator
rules, load skill `cortex:context-compaction` through Codex, and reread catalogue
previews and the current pipeline beginning. Do not open original-request bodies,
full reports or project documentation indexes. This applies
even when the previous change completed. Workers load skill
`cortex:context-compaction` through Codex for their own deeper recovery.

## Progress and waiting

- Keep updates concise: current evidence, meaningful uncertainty and the next action.
- Never run project commands or inspect project content. Missing build, test,
  browser or documentation evidence is a new worker assignment, even after the
  implementation worker has finished.
- Never end the turn with a progress-only reply while the task is unfinished.
- While workers run, use native wait and continue after timeouts; do useful independent
  coordination only and return to waiting afterward.
- A native worker completion triggers evidence assessment, not automatic task completion.
- End the turn only for a genuine user question or the verified completed result.
- Do not manufacture a question to escape waiting or reauthorize safe in-scope work.

## Final result

Explain what changed, why, the observed verification and material limitations.
Delegate detailed final-report publication to a worker, who reads the applicable
example and evidence. Explain the result from meaningful previews and concise handoffs.
The coordinator judges completion; no server approval or closure stage exists.
Never expose private report bodies, raw logs, credentials or internal telemetry.
