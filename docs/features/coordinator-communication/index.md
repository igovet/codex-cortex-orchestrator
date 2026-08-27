# Coordinator communication

<!-- GENERATED:START -->

## Purpose and boundary

Cortex 12.1.0 ships a packaged coordinator-to-user communication policy. It
keeps user-facing commentary, questions, plan summaries, progress, decision
summaries, artifact summaries, and final answers clear without creating a
runtime loader, dispatcher, lifecycle hook, tool, or backend gate.

The policy leads with result, then user impact, then the next safe step. It uses
the latest meaningful user language, suppresses unchanged wait updates, hides
opaque identifiers and ledger jargon by default, reveals technical detail
progressively, and permits only safe optional contextual humor after the
material fact. Coordinator-to-worker messages, native worker transcripts,
worker-authored report narrative, decision normalization, ledger prose, and
human-view source content remain English. Canonical product-facing
reports/handoffs may carry one optional unchanged `source_text` value as inert
source material; existing task/decision original text remains in its designated
field.

For a Russian latest meaningful user message, coordinator-to-user communication
is Russian while preserving the same outcome-first, progressively detailed
shape. Russian is a user-facing localization rule only: worker, inter-worker,
ledger, and report content remains English and opaque internal identifiers stay
hidden by default.

The canonical source is the bundled
[coordinator-communication skill](../../../plugins/cortex/skills/coordinator-communication/SKILL.md).
It is integrated with the packaged orchestrator, control, and progress-accounting
guidance. It does not weaken verified-link publication, worker ownership,
ordinary approvals, or safety boundaries.

## Deterministic verification

`scripts/cortex-prompt-lint.py`, the marketplace validator, and the targeted
release gate require the packaged skill, its front matter, policy markers, and
cross-skill integration. These are deterministic static contract checks, not
live-model, A/B, or model-quality evaluation.

<!-- GENERATED:END -->
