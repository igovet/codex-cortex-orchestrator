---
name: token-monitoring
description: Keep Codex task reporting concise and evidence-focused without collecting unnecessary token, model, or private telemetry. Use when defining task summaries, audits, or lightweight workflow metrics.
---

# Minimal Task Metrics

Track only task-relevant evidence: scope, agents used, checks run, pass/fail or blocked status, material risk, and the location of durable artifacts. Do not record token counts, hidden reasoning, secret-bearing prompts, or raw conversation transcripts in repository files. A control-plane delegation may retain its selected model and reasoning effort when that contract requires them; do not duplicate them as general task telemetry.

Prefer the parent thread and normal Git history for task progress. Create persistent metrics only when the repository explicitly needs them.
