# Coordinator communication

<!-- GENERATED:START -->

## Purpose and boundary

Cortex 1.15.3 ships a packaged coordinator-to-user communication policy. It
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

Decision-opening operations create durable holds but do not themselves render
their prompts in either CLI or Desktop. After a successful opening, the
coordinator's final answer must give the user a complete localized decision
packet: why the decision is needed, its concrete subject, the available safe
choices, and the material consequence or stopping condition of each. A user
must not need to expand a tool call or inspect worker output to understand what
is being approved.

Questions known in advance to choose product behavior, acceptance, constraints,
verification, or scope open steering before they are presented; the direct
answer is the steering answer and is not followed by a redundant confirmation.
Ordinary clarification is reserved for facts whose possible answers leave every
current outcome unchanged. When the user already states a concrete semantic
change—or a factual answer itself contains one—the coordinator records that
exact message directly as steering. It never opens another question asking the
user to confirm the same instruction.

Plan review additionally includes a decision-ready summary of scope, ordered
stages, intended changes, verification, stop/deploy conditions, and material
risks or unresolved items. A ready server-verified current-plan link is copied
byte-for-byte; if it is unavailable, the limitation is disclosed and enough
plan detail is provided inline. A bare “plan ready — approve?” message is a
contract violation.

Native workers and other subagents never ask the user directly. They publish a
partial or blocked outcome containing the blocked action, established evidence,
exact missing decision, safe choices, and consequences. The coordinator reads
that authoritative evidence and renders the complete user-facing question; it
does not forward a context-free worker request.

A host-confirmed terminal worker stop without a publication is recovered from
the current responsibility scope through one lineage-linked replacement. The
coordinator does not replay the original assignment, ask the user to say
“continue”, or end the turn while that deterministic recovery remains available.

## Deterministic verification

`scripts/cortex-prompt-lint.py`, the marketplace validator, and the targeted
release gate require the packaged skill, its front matter, policy markers, and
cross-skill integration. These are deterministic static contract checks, not
live-model, A/B, or model-quality evaluation.

<!-- GENERATED:END -->
